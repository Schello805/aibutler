import asyncio
import httpx
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.main import create_app
from backend.lighting import is_current_lighting_question, read_lights, light_answer


def obj(name,role='switch',type='boolean',**extra):
    return {'type':'state','common':{'name':name,'role':role,'type':type,**extra},'native':{'private':'never-expose'}}


def wire(monkeypatch,objects,values,enums=None):
    import backend.lighting as module
    original=httpx.AsyncClient;requests=[]
    def handler(request):
        requests.append(request)
        assert request.method=='GET'
        if request.url.path=='/objects':
            return httpx.Response(200,json=(enums or {}) if request.url.params.get('type')=='enum' else objects)
        assert request.url.path.startswith('/get/')
        id=request.url.path.removeprefix('/get/')
        if id not in values:return httpx.Response(503)
        value=values[id]
        return httpx.Response(200,json=value if isinstance(value,dict) else {'val':value,'ack':True,'q':0,'ts':123})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    return requests


@pytest.mark.parametrize('question',['Welche Lichter sind gerade noch an?','Sind die Lampen in der Küche aus?','Welche Leuchten sind eingeschaltet?','Welche Lampen sind nicht aus?'])
def test_current_lighting_intent(question):
    assert is_current_lighting_question(question)


@pytest.mark.parametrize('question',['Wann war das Licht gestern an?','Was kostet die Rechnung für die Lampe?','Welche Lichter habe ich zuletzt gekauft?','Was wollte Anna von mir?'])
def test_documents_and_history_are_not_replaced_by_current_light_query(question):
    assert not is_current_lighting_question(question)


def test_live_lights_skip_metadata_keep_more_than_twenty_and_read_each_time(monkeypatch):
    objects={f'device.{n}.STATE':obj(f'Licht {n}.STATE') for n in range(26)}
    objects.update({f'device.0.meta{n}':obj('Licht Küche.CONFIG_PENDING','indicator') for n in range(40)})
    objects['device.0.AES_KEY']=obj('Licht Küche AES key')
    objects['alias.0.special.STATE']=obj('Licht Tresor','switch.light',alias={'id':'device.0.AES_KEY'})
    objects['javascript.0.scriptEnabled.light']=obj('Licht Automatik','switch.active')
    objects['javascript.0.scriptEnabled.plain']=obj('Licht Skript','switch')
    objects['doorbell.LEVEL']=obj('Funkgong.LEVEL','level.dimmer','number')
    values={f'device.{n}.STATE':n==25 for n in range(26)}
    requests=wire(monkeypatch,objects,values)
    settings=Settings(_env_file=None,iobroker_url='http://home:8087')
    first,inventory,enums=asyncio.run(read_lights(settings,'Welche Lichter sind an?'))
    assert len(first['lights'])==26
    assert [s['name'] for s in first['lights'] if s['on']]==['Licht 25']
    assert 'private' not in str(inventory) and 'AES_KEY' not in str(inventory)
    assert not any('meta' in r.url.path or 'doorbell' in r.url.path or 'scriptEnabled' in r.url.path for r in requests)
    values['device.25.STATE']=False
    second,_,_=asyncio.run(read_lights(settings,'Welche Lichter sind an?',inventory,enums))
    assert all(s['on'] is False for s in second['lights'])
    assert sum(r.url.path=='/get/device.25.STATE' for r in requests)==2
    assert 'Alle 26' in light_answer('Welche Lichter sind an?',second)


def test_light_function_and_room_membership_alias_and_brightness_precedence(monkeypatch):
    objects={'device.0.living.on':obj('Decke','switch.light'),
             'device.0.living.bri':obj('Decke','level.dimmer','number'),
             'alias.0.living.on':obj('Wohnzimmer Deckenlampe','switch.light',alias={'id':'device.0.living.on'}),
             'device.0.kitchen.on':obj('Küchenlampe','switch.light')}
    enums={'enum.functions.light':{'common':{'name':'Beleuchtung','members':['device.0.living','device.0.kitchen']}},
           'enum.rooms.living':{'common':{'name':'Wohnzimmer','members':['device.0.living']}},
           'enum.rooms.kitchen':{'common':{'name':'Küche','members':['device.0.kitchen']}}}
    values={'device.0.living.on':False,'device.0.living.bri':180,'alias.0.living.on':False,'device.0.kitchen.on':True}
    requests=wire(monkeypatch,objects,values,enums)
    result,_,_=asyncio.run(read_lights(Settings(_env_file=None,iobroker_url='http://home:8087'),'Welche Lichter sind im Wohnzimmer an?'))
    assert len(result['lights'])==1
    assert result['lights'][0]['name']=='Wohnzimmer Deckenlampe'
    assert result['lights'][0]['on'] is False
    assert not any('kitchen' in request.url.path for request in requests)


def test_quality_unconfirmed_unreachable_and_failed_reads_never_become_off(monkeypatch):
    objects={f'device.{n}.STATE':obj(f'Licht {n}') for n in range(5)}
    objects['device.3.0.UNREACH']=obj('Erreichbarkeit','indicator.unreach')
    values={'device.0.STATE':{'val':True,'q':1,'ack':True},
            'device.1.STATE':{'val':True,'q':0,'ack':False},
            'device.2.STATE':False,'device.3.STATE':True,'device.3.0.UNREACH':True}
    wire(monkeypatch,objects,values)
    result,_,_=asyncio.run(read_lights(Settings(_env_file=None,iobroker_url='http://home:8087'),'Welche Lichter sind an?'))
    assert sum(s['on'] is None for s in result['lights'])==4
    answer=light_answer('Welche Lichter sind an?',result)
    assert 'unklar' in answer and 'Alle' not in answer


def test_unknown_room_does_not_fall_back_to_every_light(monkeypatch):
    wire(monkeypatch,{'device.0.STATE':obj('Licht Wohnzimmer')},{'device.0.STATE':True})
    result,_,_=asyncio.run(read_lights(Settings(_env_file=None,iobroker_url='http://home:8087'),'Welche Lichter sind im Wintergarten an?'))
    assert result['lights']==[]
    assert 'keine passenden' in light_answer('Welche Lichter sind im Wintergarten an?',result)


def test_dimmer_level_and_on_off_words(monkeypatch):
    wire(monkeypatch,{'dim.0.LEVEL':obj('Licht Arbeitszimmer.LEVEL','level.dimmer','number'),
                      'dim.1.LEVEL':obj('Licht Küche.LEVEL','level.dimmer','number')},
                      {'dim.0.LEVEL':0.25,'dim.1.LEVEL':0})
    result,_,_=asyncio.run(read_lights(Settings(_env_file=None,iobroker_url='http://home:8087'),'Welche Lichter sind an?'))
    assert 'Arbeitszimmer' in light_answer('Welche Lichter sind an?',result)
    assert 'Küche' in light_answer('Welche Lichter sind aus?',result)
    assert 'Arbeitszimmer' in light_answer('Welche Lichter sind nicht aus?',result)


def test_chat_uses_live_states_even_when_ai_and_background_monitoring_are_off(tmp_path,monkeypatch):
    import backend.main as main
    wire(monkeypatch,{'device.0.STATE':obj('Licht Flur.STATE')},{'device.0.STATE':True})
    async def forbidden(*args,**kwargs):raise AssertionError('No model call for direct light status')
    monkeypatch.setattr(main,'generate',forbidden);monkeypatch.setattr(main,'plan_question',forbidden)
    settings=Settings(_env_file=None,api_token='test',data_dir=tmp_path,worker_enabled=False,iobroker_url='http://home:8087',iobroker_enabled=False,chat_llm_enabled=True)
    app=create_app(settings)
    app.state.store.commit_item({'id':'irrelevant','version':'1','title':'Licht Rechnung','body':'Lampe gekauft','source':'paperless'})
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Welche Lichter sind gerade noch an?','share_external':True}).json()
        assert result['answer']=='An ist: Licht Flur.'
        assert result['answer_mode']=='live' and result['external_used'] is False and result['ai_issue'] is None
        assert [source['id'] for source in result['sources']]==['iobroker:lighting']
        assert result['sources'][0]['document_date']
        assert app.state.store.pending_notifications()==[]
        restored=client.get('/api/conversations/'+result['conversation_id'],headers={'Authorization':'Bearer test'}).json()
        assert restored['messages'][-1]['payload']['answer_mode']=='live'

def test_provider_limit_does_not_tell_user_to_enable_already_enabled_ai(tmp_path,monkeypatch):
    import backend.main as main
    from backend.intelligence import ProviderLimited
    async def unavailable(*args,**kwargs):raise ProviderLimited('HTTP 429')
    monkeypatch.setattr(main,'generate',unavailable);monkeypatch.setattr(main,'plan_question',unavailable)
    app=create_app(Settings(_env_file=None,api_token='test',data_dir=tmp_path,worker_enabled=False,chat_llm_enabled=True))
    monkeypatch.setattr(app.state.store,'search',lambda *args,**kwargs:[{'item_id':'example','source':'paperless','title':'Projektunterlagen','body':'Lokaler Projektinhalt'}])
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Was bedeutet das für mein Projekt?','share_external':True}).json()
        assert '429' in result['ai_issue']
        assert 'einschalten' not in result['answer'] and 'Aktiviere' not in result['answer']
        assert result['external_used'] is False
