import time
import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.routing import route_question
from backend.planning import RetrievalPlan,complete_plan


def test_general_and_clarification_plans_never_expand_to_all_sources():
    for intent in ('general','clarify'):
        plan=complete_plan(RetrievalPlan(intent=intent,sources=[]),'Eine Frage')
        assert plan.sources==[] and plan.queries==[]


@pytest.mark.parametrize('message',['Kannst du mich gut verstehen oder nicht so gut?','Verstehst du mich?','Hallo Butler','Wie hoch sind die Stromkosten?'])
def test_conversation_and_ambiguity_never_wait_for_connections(tmp_path,monkeypatch,message):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True))
    async def forbidden(*args,**kwargs):raise AssertionError('No provider or connection for this route')
    monkeypatch.setattr('backend.main.plan_question',forbidden);monkeypatch.setattr('backend.main.generate',forbidden)
    for name in ('search_mail_context','search_document_context','search_context','poll_ntfy','relevant_smart_states'):
        monkeypatch.setattr(app.state.worker,name,forbidden)
    monkeypatch.setattr(app.state.worker.personal,'context',forbidden)
    with TestClient(app) as client:
        started=time.monotonic()
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':message,'share_external':True}).json()
        assert result['answer'] and not result['external_used'] and result['sources']==[]
        assert time.monotonic()-started<1
        if 'Stromkosten' in message:assert result['clarification']=='electricity_costs'


def test_clarification_followup_routes_only_to_required_domains():
    last={'clarification':'electricity_costs','pending_question':'Wie hoch sind meine Stromkosten?'}
    monthly=route_question('Den monatlichen Abschlag',last)
    assert monthly['plan'].sources==['email','paperless']
    assert 'Strom' in monthly['message']
    device=route_question('Den Kühlschrank diese Woche',last)
    assert device['plan'].sources==['smart_home','paperless']
    assert route_question('Für ein bestimmtes Gerät',last)['kind']=='clarify'
    assert route_question('Die tatsächlichen Kosten dieses Jahr',last)['plan'].sources==['paperless','smart_home']
    assert route_question('Was ist Photosynthese?')['kind']=='general'
    assert route_question('Was ist Photosynthese?',last)['kind']=='general'
    assert route_question('Was steht morgen im Kalender?')['plan'].sources==['calendar']


def test_ambiguous_cost_then_monthly_advance_uses_index_without_live_wait(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True))
    app.state.store.commit_item({'id':'advance','version':'1','source':'paperless','title':'Strom Abschlag Vorauszahlung','body':'Der monatliche Strom Abschlag beträgt ab November 100 Euro.'})
    calls=[]
    async def forbidden(*args,**kwargs):raise AssertionError('No general or live query for indexed advance')
    async def answer(settings,task,payload):
        calls.append('answer');assert payload['retrieval_mode']=='indexed'
        assert payload['sources'][0]['item_id']=='advance'
        return 'Laut dem hinterlegten Schreiben beträgt dein Abschlag 100 Euro monatlich.'
    monkeypatch.setattr('backend.main.plan_question',forbidden);monkeypatch.setattr('backend.main.generate',answer)
    for name in ('search_mail_context','search_document_context','search_context','poll_ntfy','relevant_smart_states'):
        monkeypatch.setattr(app.state.worker,name,forbidden)
    monkeypatch.setattr(app.state.worker.personal,'context',forbidden)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'}
        first=client.post('/api/chat',headers=headers,json={'message':'Wie hoch sind meine Stromkosten?','share_external':True}).json()
        assert first['clarification']=='electricity_costs'
        second=client.post('/api/chat',headers=headers,json={'message':'Den monatlichen Abschlag','conversation_id':first['conversation_id'],'share_external':True}).json()
        assert second['retrieval_mode']=='indexed' and second['external_used']
        assert '100 Euro' in second['answer'] and calls==['answer']
        assert second['duration_seconds']<1


def test_device_and_period_clarification_preserves_device():
    first=route_question('Wie hoch sind die Stromkosten des Kühlschranks?')
    assert first['kind']=='clarify' and first['device_question']
    follow=route_question('Diese Woche',first)
    assert follow['kind']=='sources' and follow['plan'].sources==['smart_home','paperless']
    assert 'Kühlschrank' in follow['message']


def test_research_deadline_returns_saved_failure(tmp_path,monkeypatch):
    import asyncio
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True))
    real_wait=asyncio.wait_for
    async def deadline(operation,timeout):return await real_wait(operation,.1 if timeout==180 else timeout)
    async def slow_plan(*args,**kwargs):await asyncio.Event().wait()
    monkeypatch.setattr('backend.main.asyncio.wait_for',deadline)
    monkeypatch.setattr('backend.main.plan_question',slow_plan)
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Was sollte ich beachten?','request_id':'f'*32,'share_external':True}).json()
        assert result['interrupted'] and '180 Sekunden' in result['answer']
        state=client.get('/api/chat/result/'+'f'*32,headers={'Authorization':'Bearer test'}).json()
        assert state['status']=='failed' and state['result']['interrupted']
