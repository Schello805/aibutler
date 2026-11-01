import asyncio
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import Settings


def test_missing_location_asks_before_llm_or_source_search_and_accepts_reply(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,weather_enabled=True))
    async def forbidden(*args,**kwargs):raise AssertionError('No long search before clarification')
    monkeypatch.setattr('backend.main.plan_question',forbidden)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'}
        first=client.post('/api/chat',headers=headers,json={'message':'Wie wird das Wetter morgen?','share_external':True}).json()
        assert first['clarification']=='location' and 'Für welchen Ort' in first['answer']
        calls=[]
        async def locations(query):
            calls.append(query)
            return [{'latitude':52.52,'longitude':13.4,'country':'DE','subdivision':'DE-BE','label':'Berlin','timezone':'Europe/Berlin'}]
        async def public_context(question,location,history):
            assert question=='Wie wird das Wetter morgen?' and location.label=='Berlin'
            return {'sources':[],'diagnostics':[],'weather':{'date':'2026-11-02','stale':False,'hours':[{'temperature_2m':10,'precipitation_probability':0}]}}
        monkeypatch.setattr(app.state.public_data,'locations',locations);monkeypatch.setattr(app.state.public_data,'context',public_context)
        second=client.post('/api/chat',headers=headers,json={'message':'In Berlin','conversation_id':first['conversation_id']}).json()
        assert calls==['Berlin'] and second.get('clarification') is None
        assert '10' in second['answer']
        history=client.get('/api/conversations/'+second['conversation_id'],headers=headers).json()
        assert history['messages'][-2]['text']=='In Berlin'


def test_icloud_test_timeout_reports_deadline_and_clears_running_status(tmp_path,monkeypatch):
    import backend.connections as connections
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,icloud_calendar_enabled=True,icloud_calendar_user='test@example.invalid',icloud_calendar_password='test-app-password'))
    async def refresh(*args,**kwargs):await asyncio.Event().wait()
    monkeypatch.setattr('backend.icloud.DAV.read',refresh)
    real=asyncio.wait_for
    async def deadline(operation,timeout):
        return await real(operation,.01 if timeout==20 else timeout)
    monkeypatch.setattr(connections.asyncio,'wait_for',deadline)
    with TestClient(app) as client:
        result=client.post('/api/connections/icloud_calendar/test',headers={'Authorization':'Bearer test'})
        assert result.status_code==504 and '20 Sekunden' in result.json()['detail']
        assert app.state.worker.personal.status['icloud_calendar']['running'] is False


def test_missing_region_after_reverse_geocoder_failure_asks_directly(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,weather_enabled=True,holidays_enabled=True,chat_llm_enabled=True))
    async def forbidden(*args,**kwargs):raise AssertionError('Must ask before provider calls')
    monkeypatch.setattr('backend.main.plan_question',forbidden)
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Kann ich morgen draußen arbeiten?','share_external':True,'device_location':{'latitude':52.52,'longitude':13.4}}).json()
        assert result['clarification']=='location'
        assert 'Bundesland' in result['answer']


def test_city_and_state_reply_filters_ambiguous_geocoder_results(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    async def geocode(url,params):
        assert params['name']=='Bechhofen'
        return {'results':[{'name':'Bechhofen','latitude':49.1,'longitude':10.5,'country_code':'DE','admin1':'Bayern'},{'name':'Bechhofen','latitude':49.3,'longitude':7.3,'country_code':'DE','admin1':'Rheinland-Pfalz'}]}
    monkeypatch.setattr(app.state.public_data,'get_json',geocode)
    result=asyncio.run(app.state.public_data.locations('Bechhofen, Bayern'))
    assert len(result)==1 and result[0]['subdivision']=='DE-BY'
