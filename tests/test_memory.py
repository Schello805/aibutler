from datetime import date,timedelta
import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.memory import Memories,today,candidate
from backend.store import Store


def setup(tmp_path,monkeypatch,**settings):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,semantic_enabled=False,**settings))
    monkeypatch.setattr(app.state.accounts,'session',lambda token:{'id':token,'email':'test@example.invalid','role':'member'} if token else None)
    return app

def headers(owner='a'):return {'Cookie':'butler_session='+owner,'Origin':'https://testserver'}

def chat(client,text,conversation_id=None,owner='a',**kwargs):
    response=client.post('/api/chat',headers=headers(owner),json={'message':text,'conversation_id':conversation_id,**kwargs})
    assert response.status_code==200,response.text
    return response.json()


def test_explicit_memory_is_local_persistent_and_account_private(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    async def forbidden(*args,**kwargs):raise AssertionError('Explicit memory needs no external model')
    monkeypatch.setattr('backend.main.generate',forbidden)
    with TestClient(app,base_url='https://testserver') as client:
        result=chat(client,'Merk dir: Ich arbeite bei Firma Nord.')
        identifier=result['memory_saved']
        assert client.get('/api/memories',headers=headers('b')).json()['memories']==[]
        assert client.delete('/api/memories/'+identifier,headers=headers('b')).status_code==404
        assert client.post('/api/memories/'+identifier+'/confirm',headers=headers('b')).status_code==404
        row=client.get('/api/memories',headers=headers()).json()['memories'][0]
        assert row['status']=='active' and row['slot']=='employer' and row['conversation_id']==result['conversation_id']
        assert client.get('/api/memories',headers={'Authorization':'Bearer test'}).status_code==403
        assert app.state.store.search('Firma Nord')==[]
        assert app.state.store.events()==[]
    restarted=Memories(Store(tmp_path))
    assert restarted.retrieve('a','Wer ist mein Arbeitgeber?')[0]['content']=='Ich arbeite bei Firma Nord.'
    assert restarted.retrieve('b','Wer ist mein Arbeitgeber?')==[]


def test_proposal_requires_confirmation_and_survives_new_conversation(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://testserver') as client:
        proposed=chat(client,'Ich möchte einen Hühnerstall bauen.')
        assert proposed['memory_proposal']['kind']=='project'
        assert app.state.memories.retrieve('a','Kann ich morgen draußen arbeiten?')==[]
        confirmed=chat(client,'Ja',proposed['conversation_id'])
        assert 'Gemerkt' in confirmed['answer']
        assert app.state.memories.retrieve('a','Kann ich morgen draußen arbeiten?')[0]['kind']=='project'
        recalled=chat(client,'Was hast du dir gemerkt?')
        assert 'Hühnerstall' in recalled['answer']
        identifier=proposed['memory_proposal']['id']
        assert client.post('/api/memories/'+identifier+'/reject',headers=headers()).status_code==409
        assert app.state.memories.get('a',identifier)['status']=='active'


def test_conflicting_employer_is_replaced_only_after_yes(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://testserver') as client:
        old=chat(client,'Merk dir: Ich arbeite bei Firma Nord.')['memory_saved']
        new=chat(client,'Merk dir: Ich arbeite bei Firma Süd.')
        assert new['memory_proposal']['conflicts'][0]['id']==old
        assert app.state.memories.get('a',old)['status']=='active'
        chat(client,'Ja',new['conversation_id'])
        assert app.state.memories.get('a',old)['status']=='superseded'
        assert app.state.memories.retrieve('a','Wer ist mein Arbeitgeber?')[0]['content']=='Ich arbeite bei Firma Süd.'
        assert 'Firma Süd' in chat(client,'Wer ist mein Arbeitgeber?')['answer']


def test_manual_edit_validity_and_revision_guards(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://testserver') as client:
        row=client.post('/api/memories',headers=headers(),json={'content':'Ich wohne in Berlin.','kind':'fact'}).json()
        edit={'content':'Ich wohne in Hamburg.','kind':'fact','revision':row['revision'],'valid_until':(date.fromisoformat(today())-timedelta(days=1)).isoformat()}
        assert client.put('/api/memories/'+row['id'],headers=headers('b'),json=edit).status_code==404
        assert client.put('/api/memories/'+row['id'],headers=headers(),json=edit).status_code==200
        assert client.put('/api/memories/'+row['id'],headers=headers(),json=edit).status_code==409
        assert app.state.memories.retrieve('a','Wo wohne ich?')==[]
        assert client.delete('/api/memories/'+row['id'],headers=headers()).status_code==200
        assert app.state.memories.get('a',row['id']) is None
        assert client.post('/api/memories',headers=headers(),json={'content':'Test','valid_from':'2027-02-02','valid_until':'2027-01-01'}).status_code==422


def test_scheduled_change_preserves_current_fact_until_start(tmp_path):
    memory=Memories(Store(tmp_path))
    old,_=memory.create('a',{'content':'Ich arbeite bei Firma Nord.','kind':'fact'})
    future=date.fromisoformat(today())+timedelta(days=60)
    new,_=memory.create('a',{'content':'Ich arbeite bei Firma Süd.','kind':'fact','valid_from':future})
    assert new['status']=='pending'
    memory.confirm('a',new['id'])
    assert memory.retrieve('a','Wer ist mein Arbeitgeber?')[0]['id']==old['id']
    assert memory.get('a',old['id'])['valid_until']==(future-timedelta(days=1)).isoformat()


def test_historical_suggestions_keep_source_date_and_never_auto_activate(tmp_path):
    store=Store(tmp_path);memory=Memories(store)
    store.save_exchange(None,'Ich wohne in Berlin.',{'answer':'Okay'},'a')
    store.save_exchange(None,'Merke dir, dass ich bei Firma Nord arbeite.',{'answer':'Okay'},'a')
    store.save_exchange(None,'Ich arbeite bei Firma Privat.',{'answer':'Okay'},'b')
    assert memory.discover('a')==2 and memory.discover('a')==0
    row=memory.list('a')[0]
    assert row['status']=='pending' and row['source_date']
    assert memory.retrieve('a','Wo wohne ich?')==[]
    assert memory.list('b')==[]
    assert not candidate('In der E-Mail steht: Ich arbeite bei Firma X.')
    assert not candidate('Wo wohne ich?')


def test_secret_is_not_added_to_memory_or_echoed_in_response(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://testserver') as client:
        result=chat(client,'Merk dir: Mein Passwort ist privates-test-passwort')
        assert 'privates-test-passwort' not in result['answer']
        assert client.get('/api/memories',headers=headers()).json()['memories']==[]


def test_only_selected_confirmed_memories_reach_chat_model(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch,chat_llm_enabled=True)
    memory=app.state.memories
    memory.create('a',{'content':'Bitte antworte kurz.','kind':'preference'})
    memory.create('a',{'content':'Ich wohne in Berlin.','kind':'fact'},pending=True)
    memory.create('b',{'content':'Anderes privates Konto','kind':'preference'})
    seen=[]
    async def plan(*args,**kwargs):return None
    async def generate(settings,task,payload):seen.append(payload);return 'Kurze Antwort.'
    monkeypatch.setattr('backend.main.plan_question',plan);monkeypatch.setattr('backend.main.generate',generate)
    with TestClient(app,base_url='https://testserver') as client:
        answer=chat(client,'Wie kannst du mir helfen?',share_external=True)
        assert answer['external_used']
    assert seen and [row['content'] for row in seen[-1]['memories']]==['Bitte antworte kurz.']


def test_previous_proposal_displays_current_confirmation_state(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://testserver') as client:
        proposal=chat(client,'Ich wohne in Berlin.')
        identifier=proposal['memory_proposal']['id']
        assert client.post('/api/memories/'+identifier+'/confirm',headers=headers()).status_code==200
        messages=client.get('/api/conversations/'+proposal['conversation_id'],headers=headers()).json()['messages']
        assert messages[-1]['payload']['memory_proposal']['status']=='active'
        assert 'Berlin' in chat(client,'Wo wohne ich?')['answer']
        assert client.post('/api/memories/'+identifier+'/archive',headers=headers()).status_code==200
        assert app.state.memories.retrieve('a','Wo wohne ich?')==[]


def test_german_subordinate_clause_and_unclear_future_date(tmp_path):
    memory=Memories(Store(tmp_path))
    result=memory.chat('a','Merke dir, dass ich bei Firma Nord arbeite.',None,{})
    old=memory.get('a',result['memory_saved'])
    assert old['slot']=='employer'
    new,_=memory.create('a',{'content':'Ab nächstem Frühjahr arbeite ich bei Firma Süd.','kind':'fact'})
    assert new['status']=='pending'
    with pytest.raises(ValueError,match='Datum'):memory.confirm('a',new['id'])
    assert memory.get('a',old['id'])['status']=='active'


def test_memory_commands_never_request_device_location(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch,weather_enabled=True,holidays_enabled=True)
    with TestClient(app,base_url='https://testserver') as client:
        result=client.post('/api/chat/location-needed',headers=headers(),json={'message':'Merk dir: Ich möchte morgen draußen arbeiten.'})
        assert result.json()=={'needed':False}
        assert 'memory_saved' in chat(client,'Merk dir:Ich bevorzuge kurze Antworten.')
    assert not candidate('Ich möchte wissen, ob es morgen regnet.')
    assert not candidate('Ich möchte, dass du meine E-Mails suchst.')


def test_spoken_completion_needs_confirmation_and_stops_project_context(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://testserver') as client:
        identifier=chat(client,'Merk dir: Ich möchte einen Hühnerstall bauen.')['memory_saved']
        proposal=chat(client,'Der Hühnerstall ist fertig.')
        assert proposal['memory_proposal']['action']=='archive'
        assert app.state.memories.get('a',identifier)['status']=='active'
        chat(client,'Ja',proposal['conversation_id'])
        assert app.state.memories.get('a',identifier)['status']=='archived'
        assert app.state.memories.retrieve('a','Kann ich morgen draußen arbeiten?')==[]


def test_rejected_secret_is_not_forwarded_as_later_chat_history(tmp_path,monkeypatch):
    app=setup(tmp_path,monkeypatch,chat_llm_enabled=True)
    planned=[];generated=[]
    async def plan(settings,text):planned.append(text);return None
    async def generate(settings,task,payload):generated.append(payload);return 'Antwort.'
    monkeypatch.setattr('backend.main.plan_question',plan);monkeypatch.setattr('backend.main.generate',generate)
    with TestClient(app,base_url='https://testserver') as client:
        first=chat(client,'Merk dir: Mein Passwort ist geheimes-beispiel-passwort')
        chat(client,'Wie kannst du mir helfen?',first['conversation_id'],share_external=True)
    assert 'geheimes-beispiel-passwort' not in str(planned)+str(generated)


def test_independent_preferences_do_not_replace_each_other(tmp_path):
    memory=Memories(Store(tmp_path))
    first,_=memory.create('a',{'content':'Bitte antworte kurz.','kind':'preference'})
    second,_=memory.create('a',{'content':'Sprich auf Deutsch.','kind':'preference'})
    assert first['status']==second['status']=='active'
    assert first['slot']!=second['slot']
    favorite,_=memory.create('a',{'content':'Mein Lieblingsessen ist Pizza.','kind':'preference'})
    new,_=memory.create('a',{'content':'Mein Lieblingsessen ist Pasta.','kind':'preference'})
    assert new['status']=='pending'
    memory.confirm('a',new['id'])
    assert memory.get('a',favorite['id'])['status']=='superseded'


def test_answer_timing_survives_history_and_duplicate_request(tmp_path,monkeypatch):
    from datetime import datetime
    app=setup(tmp_path,monkeypatch)
    with TestClient(app,base_url='https://testserver') as client:
        result=chat(client,'Merk dir: Ich bevorzuge kurze Antworten.',request_id='c'*32)
        assert result['duration_seconds']>=0
        assert datetime.fromisoformat(result['answered_at']).tzinfo is not None
        history=client.get('/api/conversations/'+result['conversation_id'],headers=headers()).json()['messages']
        assert history[0]['created']
        assert history[-1]['payload']['answered_at']==result['answered_at']
        assert history[-1]['payload']['duration_seconds']==result['duration_seconds']
        replay=chat(client,'Merk dir: Ich bevorzuge kurze Antworten.',conversation_id=result['conversation_id'],request_id='c'*32)
        assert replay['answered_at']==result['answered_at']
