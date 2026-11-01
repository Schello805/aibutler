import asyncio
import json
import httpx
import pytest
from fastapi import HTTPException
from backend.config import Settings
from backend.controls import ControlService,Device,control_intent
from backend.store import Store

BLIND={'name':'Rollo Wohnzimmer','kind':'blind','state_id':'hm-rpc.0.ROLL:1.LEVEL','stop_id':'hm-rpc.0.ROLL:1.STOP','open_value':0,'closed_value':1}

def service(tmp_path,enabled=True):
    settings=Settings(_env_file=None,data_dir=tmp_path,worker_enabled=False,iobroker_url='http://home:8087',iobroker_control_enabled=enabled,iobroker_controls=json.dumps([BLIND]))
    return ControlService(settings,Store(tmp_path))

@pytest.mark.parametrize('question',['Sind die Rollos unten?','Bitte nicht das Rollo Wohnzimmer hochfahren','Schalte das Licht nicht aus','Fahre alle Rollos herunter','Wenn es regnet, fahre das Rollo runter','Fahre Rollo Wohnzimmer hoch und schalte Licht ein'])
def test_no_actions_from_questions_negation_or_groups(question):assert control_intent(question) is None

def test_explicit_confirmation_and_owner_binding(tmp_path):
    control=service(tmp_path)
    result=control.plan('Fahre das Rollo im Wohnzimmer auf 50 Prozent','alice')
    assert result['control_action'] and not result['external_used']
    token=result['control_action']['token']
    with pytest.raises(HTTPException) as error:asyncio.run(control.execute(token,'bob'))
    assert error.value.status_code==404 and token in control.pending
    control.settings.iobroker_control_enabled=False
    with pytest.raises(HTTPException) as error:asyncio.run(control.execute(token,'alice'))
    assert error.value.status_code==409

def test_disabled_or_unknown_devices_never_offer_execution(tmp_path):
    assert 'control_action' not in service(tmp_path,False).plan('Fahre Rollo Wohnzimmer hoch','alice')
    control=service(tmp_path)
    assert 'control_action' not in control.plan('Fahre Rollo Schlafzimmer hoch','alice')
    assert 'control_action' not in control.plan('Fahre Rollo Wohnzimmer auf 101 Prozent','alice')

@pytest.mark.parametrize('command,expected',[
 ('Fahre das Rollo im Wohnzimmer auf 50 Prozent',.5),
 ('Fahre Rollo Wohnzimmer hoch',0),('Fahre Rollo Wohnzimmer runter',1),
 ('Stoppe Rollo Wohnzimmer',True)])
def test_calibrated_blind_mapping_post_and_single_use(tmp_path,monkeypatch,command,expected):
    control=service(tmp_path);calls=[];writes=[]
    state_id=BLIND['stop_id'] if expected is True else BLIND['state_id']
    def handler(request):
        calls.append(request)
        if request.method=='POST':
            assert request.url.path=='/setBulk'
            assert request.headers['content-type']=='text/plain'
            key,value=request.content.decode().split('=');assert key==state_id
            value=json.loads(value);assert value==expected;writes.append(value)
            return httpx.Response(200,json=[{'id':state_id,'val':value}])
        assert request.url.path=='/get/'+state_id
        return httpx.Response(200,json={'val':expected,'ack':bool(writes),'common':{'write':True,'type':'boolean' if expected is True else 'number','role':'button.stop' if expected is True else 'level.blind','min':0,'max':1}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    token=control.plan(command,'alice')['control_action']['token']
    result=asyncio.run(control.execute(token,'alice'))
    assert result['confirmed'] is (expected is not True)
    with pytest.raises(HTTPException):asyncio.run(control.execute(token,'alice'))
    assert len(writes)==1

def test_uncertain_write_is_not_repeated(tmp_path,monkeypatch):
    control=service(tmp_path);writes=[]
    def handler(request):
        if request.method=='POST':writes.append(request);raise httpx.ReadTimeout('private secret',request=request)
        return httpx.Response(200,json={'common':{'write':True,'type':'number','role':'level.blind','min':0,'max':1}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    token=control.plan('Fahre Rollo Wohnzimmer hoch','alice')['control_action']['token']
    with pytest.raises(HTTPException) as error:asyncio.run(control.execute(token,'alice'))
    assert 'Möglicherweise' in error.value.detail and 'private secret' not in error.value.detail
    with pytest.raises(HTTPException):asyncio.run(control.execute(token,'alice'))
    assert len(writes)==1

@pytest.mark.parametrize('state_id',['system.adapter.admin.0.alive','javascript.0.script','foo=1','foo&bar','foo/../../set','secret.token'])
def test_forbidden_identifiers(state_id):
    with pytest.raises(ValueError):Device(**{**BLIND,'state_id':state_id})

def test_incompatible_actuator_role_cannot_be_written(tmp_path,monkeypatch):
    control=service(tmp_path);calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json={'common':{'write':True,'type':'boolean','role':'switch.lock'}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    token=control.plan('Fahre Rollo Wohnzimmer hoch','alice')['control_action']['token']
    with pytest.raises(HTTPException):asyncio.run(control.execute(token,'alice'))
    assert all(request.method=='GET' for request in calls)

def test_switch_bool_and_expired_plan(tmp_path,monkeypatch):
    control=service(tmp_path)
    control.settings.iobroker_controls=json.dumps([{'name':'Lampe Büro','kind':'switch','state_id':'zigbee.0.lamp.state'}])
    writes=[]
    def handler(request):
        if request.method=='POST':
            assert request.content==b'zigbee.0.lamp.state=true';writes.append(request)
            return httpx.Response(200,json=[{'id':'zigbee.0.lamp.state','val':True}])
        return httpx.Response(200,json={'val':True,'ack':True,'common':{'type':'boolean','role':'switch.light','write':True}})
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    token=control.plan('Schalte die Lampe im Büro ein','alice')['control_action']['token']
    assert asyncio.run(control.execute(token,'alice'))['confirmed'] is True
    token=control.plan('Schalte Lampe Büro aus','alice')['control_action']['token']
    control.pending[token]['expires']=0
    with pytest.raises(HTTPException):asyncio.run(control.execute(token,'alice'))
    assert len(writes)==1

def test_chat_command_and_confirm_endpoint_authentication(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    settings=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,iobroker_url='http://home:8087',iobroker_control_enabled=True,iobroker_controls=json.dumps([BLIND]))
    app=create_app(settings)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'}
        result=client.post('/api/chat',headers=headers,json={'message':'Fahre Rollo Wohnzimmer auf 50 Prozent'}).json()
        token=result['control_action']['token']
        assert not result['external_used'] and '50 %' in result['answer']
        assert client.post('/api/controls/execute',json={'token':token}).status_code==401
        assert client.post('/api/controls/cancel',headers=headers,json={'token':token}).status_code==200
        assert client.post('/api/controls/execute',headers=headers,json={'token':token}).status_code==404
