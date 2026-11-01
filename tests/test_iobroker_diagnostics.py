import httpx
import pytest
from backend.config import Settings
from backend.smarthome import iobroker_error

@pytest.mark.parametrize('kind,code,expected',[
 ('http',401,'Leserechte'),('http',404,'8081'),('http',503,'503'),
 ('connect',None,'LXC'),('timeout',None,'rechtzeitig'),('format',None,'Zustandsliste')])
def test_diagnostics_are_actionable_without_leaking_secrets(kind,code,expected):
    request=httpx.Request('GET','http://private-host/states?token=secret-value')
    if kind=='http':error=httpx.HTTPStatusError('secret-value',request=request,response=httpx.Response(code,request=request))
    elif kind=='connect':error=httpx.ConnectError('secret-value',request=request)
    elif kind=='timeout':error=httpx.ReadTimeout('secret-value',request=request)
    else:error=ValueError('secret-value')
    message=iobroker_error(error,Settings(_env_file=None,iobroker_url='http://private-host:8081'))
    assert expected in message
    assert 'secret-value' not in message and 'private-host' not in message

def test_successful_test_replaces_old_background_error_and_updates_snapshot(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import create_app
    from backend import worker as module
    from backend.smarthome import scope
    settings=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,iobroker_url='http://home:8087')
    app=create_app(settings)
    app.state.worker.status['sources']['iobroker']={'error':'old failure'}
    async def read(settings):return [{'id':'lamp.online','name':'Lampe','value':True,'status':'online'}]
    monkeypatch.setattr(module,'read_iobroker',read)
    with TestClient(app) as client:
        response=client.post('/api/connections/iobroker/test',headers={'Authorization':'Bearer test'})
        assert response.status_code==200 and response.json()['ok']
        health=app.state.worker.status['sources']['iobroker']
        assert health['connected'] and 'error' not in health and not health.get('warning')
        assert app.state.store.smart_snapshot(scope(settings))['states'][0]['id']=='lamp.online'

def test_storage_failure_does_not_claim_connection_failed(tmp_path,monkeypatch):
    import asyncio
    import sqlite3
    from backend import worker as module
    from backend.store import Store
    settings=Settings(_env_file=None,data_dir=tmp_path,iobroker_url='http://home:8087',iobroker_enabled=True)
    worker=module.Worker(settings,Store(tmp_path))
    async def read(settings):return []
    def save(*args):raise sqlite3.OperationalError('database is locked private content')
    monkeypatch.setattr(module,'read_iobroker',read)
    monkeypatch.setattr(worker.store,'save_smart_snapshot',save)
    result=asyncio.run(worker.poll_iobroker())
    assert result['connected'] and result['storage_error']
    assert 'error' not in result and 'lokal' in result['warning']
    assert 'private content' not in str(result)
