import asyncio
from datetime import datetime,timedelta,date
from zoneinfo import ZoneInfo
import json
import time
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from backend.config import Settings
from backend.main import create_app
from backend.public_data import PublicData,DeviceLocation,target_day,needs_public_context
from backend.store import Store

LOCATION={'latitude':49.153876,'longitude':10.547125,'country':'DE','subdivision':'DE-BY','label':'Beispielort','timezone':'Europe/Berlin'}


def forecast(day):
    return {'timezone':'Europe/Berlin','hourly_units':{},'hourly':{'time':[day.isoformat()+'T09:00',day.isoformat()+'T15:00'],'temperature_2m':[12,17],'precipitation_probability':[10,20],'precipitation':[0,0],'wind_speed_10m':[8,12],'wind_gusts_10m':[15,20]}}


def make(tmp_path,**settings):
    s=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,**settings)
    return s,PublicData(s,Store(tmp_path))


def test_location_rounding_and_validation():
    location=DeviceLocation(**LOCATION)
    assert location.latitude==49.15 and location.longitude==10.55
    for values in ({'latitude':float('nan')},{'longitude':181},{'timezone':'../../etc/passwd'}):
        with pytest.raises(ValidationError):DeviceLocation(**{**LOCATION,**values})
    assert target_day('übermorgen',date(2026,12,31))==date(2027,1,2)
    assert target_day('morgen',date(2026,12,31))==date(2027,1,1)
    assert needs_public_context('Was wollte Tanja zuletzt?')==(False,False)
    assert needs_public_context('Kann ich morgen draußen arbeiten?')==(True,True)


def test_provider_receives_only_rounded_location_and_public_parameters(tmp_path,monkeypatch):
    _,provider=make(tmp_path,weather_enabled=True)
    tomorrow=datetime.now(ZoneInfo('Europe/Berlin')).date()+timedelta(days=1)
    calls=[]
    async def get(url,params=None):
        calls.append((url,params));return forecast(tomorrow)
    monkeypatch.setattr(provider,'get_json',get)
    async def run():
        first=await provider.context('Kann ich morgen draußen arbeiten?',DeviceLocation(**LOCATION))
        second=await provider.context('Wie wird das Wetter morgen?',DeviceLocation(**LOCATION))
        assert first['weather']['hours']==second['weather']['hours']
    asyncio.run(run())
    assert len(calls)==1
    url,params=calls[0]
    assert url=='https://api.open-meteo.com/v1/forecast'
    assert params['latitude']==49.15 and params['longitude']==10.55
    assert not any(word in json.dumps(params) for word in ('Hühnerstall','Frau','draußen'))
    assert provider.store.sync_overview()['indexed']=={} and provider.store.events()==[]


def test_unavailable_provider_marks_cache_stale_and_does_not_invent_weather(tmp_path,monkeypatch):
    _,provider=make(tmp_path,weather_enabled=True)
    today=datetime.now(ZoneInfo('Europe/Berlin')).date()
    async def get(*args):return forecast(today)
    monkeypatch.setattr(provider,'get_json',get)
    asyncio.run(provider.context('Wetter heute?',DeviceLocation(**LOCATION)))
    with provider.store.connect() as db:db.execute('UPDATE public_cache SET checked=?',(time.time()-1000,))
    async def offline(*args):raise RuntimeError('private provider details')
    monkeypatch.setattr(provider,'get_json',offline)
    result=asyncio.run(provider.context('Wetter heute?',DeviceLocation(**LOCATION)))
    assert result['weather']['stale']
    assert 'VERALTETER CACHE' in result['sources'][0]['excerpt']
    assert 'private provider details' not in str(result)


def test_holidays_filter_state_and_public_type(tmp_path,monkeypatch):
    _,provider=make(tmp_path,holidays_enabled=True)
    calls=[]
    async def get(url,params=None):
        calls.append(url)
        return [{'date':'2026-11-01','name':'Allerheiligen','nationalHoliday':False,'subdivisionCodes':['DE-BY'],'holidayTypes':['Public']},{'date':'2026-11-01','name':'Only observation','nationalHoliday':True,'holidayTypes':['Observance']}]
    monkeypatch.setattr(provider,'get_json',get)
    bavaria=asyncio.run(provider.context('Ist am 01.11.2026 Feiertag?',DeviceLocation(**LOCATION)))
    berlin=asyncio.run(provider.context('Ist am 01.11.2026 Feiertag?',DeviceLocation(**{**LOCATION,'subdivision':'DE-BE'})))
    assert [row['name'] for row in bavaria['holidays']['holidays']]==['Allerheiligen']
    assert berlin['holidays']['holidays']==[]
    assert not bavaria['holidays']['municipal_complete']
    assert calls==['https://nagerholidays.com/api/v4/Holidays/DE/2026']


def test_missing_location_and_disabled_connections_never_call_provider(tmp_path,monkeypatch):
    s,provider=make(tmp_path)
    async def forbidden(*args):raise AssertionError('unexpected request')
    monkeypatch.setattr(provider,'get_json',forbidden)
    result=asyncio.run(provider.context('Wetter morgen?',DeviceLocation(**LOCATION)))
    assert not result['sources']
    s.weather_enabled=True
    assert asyncio.run(provider.context('Wetter morgen?'))['location_needed']
    assert asyncio.run(provider.context('Was wollte Tanja?',DeviceLocation(**LOCATION)))['sources']==[]


def test_outdoor_question_combines_past_mail_weather_and_holidays(tmp_path,monkeypatch):
    import backend.main as main
    s,_=make(tmp_path,weather_enabled=True,holidays_enabled=True,chat_llm_enabled=True)
    app=create_app(s);store=app.state.store
    store.commit_item({'id':'mail:project','version':'1','source':'imap','title':'Hühnerstall bauen','body':'Kannst du bitte einen Hühnerstall im Garten bauen?','document_date':'2026-08-01'})
    tomorrow=datetime.now(ZoneInfo('Europe/Berlin')).date()+timedelta(days=1)
    sent=[]
    async def get(url,params=None):
        sent.append((url,params))
        return forecast(tomorrow) if 'open-meteo' in url else []
    async def failed_plan(*args):raise RuntimeError('planning offline')
    async def answer(settings,task,payload):
        assert any('Hühnerstall' in row['body'] for row in payload['activity_evidence'])
        assert payload['public_context']['weather']['hours']
        assert 'holidays' in payload['public_context']
        assert payload['public_context']['location']['label']=='Beispielort'
        assert 'Gerätestandort ist nicht automatisch der Projektstandort' in task
        assert 'Beziehungsrollen' in task and 'mehreren plausiblen Projekten' in task
        return 'Falls du den Hühnerstall meinst: Morgen sieht es am Gerätestandort überwiegend trocken aus. Ist der Garten auch dort?'
    monkeypatch.setattr(app.state.public_data,'get_json',get)
    monkeypatch.setattr(main,'plan_question',failed_plan);monkeypatch.setattr(main,'generate',answer)
    with TestClient(app) as client:
        response=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Kann ich morgen draußen arbeiten?','device_location':LOCATION,'share_external':True})
    result=response.json();assert response.status_code==200 and result['external_used']
    assert {'imap','weather','holidays'}<={row['source'] for row in result['sources']}
    assert not store.events()
    assert all('Hühnerstall' not in str(call) for call in sent)


def test_activity_recall_filters_ads_and_keeps_completion_updates(tmp_path):
    store=Store(tmp_path)
    for identifier,title,body,day in [('request','Hühnerstall bauen','Kannst du bitte einen Hühnerstall bauen?','2026-08-01'),('done','Hühnerstall fertig','Der Hühnerstall ist fertig gebaut.','2026-09-01'),('spam','Newsletter Garten','Bitte bauen Sie jetzt Ihren Garten um!','2026-10-01')]:
        store.commit_item({'id':identifier,'title':title,'body':body,'source':'imap','version':'1','document_date':day})
    hits=store.activity_context()
    assert {row['item_id'] for row in hits}=={'request','done'}
    assert hits[0]['activity_status']=='completed_or_cancelled'


def test_location_preflight_requires_no_location_for_unrelated_chat(tmp_path):
    s,_=make(tmp_path,weather_enabled=True)
    with TestClient(create_app(s)) as client:
        headers={'Authorization':'Bearer test'}
        assert client.post('/api/chat/location-needed',headers=headers,json={'message':'Was wollte Tanja?'}).json()=={'needed':False}
        assert client.post('/api/chat/location-needed',headers=headers,json={'message':'Kann ich morgen draußen arbeiten?'}).json()=={'needed':True}
        assert client.post('/api/chat/location-needed',json={'message':'Wetter?'}).status_code==401


def test_public_json_fetch_rejects_unknown_origins_and_large_responses(tmp_path,monkeypatch):
    import httpx
    _,provider=make(tmp_path)
    with pytest.raises(ValueError):asyncio.run(provider.get_json('http://127.0.0.1/private'))
    original=httpx.AsyncClient
    transport=httpx.MockTransport(lambda request:httpx.Response(200,content=b'x'*(1024*1024+1)))
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=transport,**kwargs))
    with pytest.raises(ValueError,match='groß'):asyncio.run(provider.get_json('https://api.open-meteo.com/v1/forecast'))


def test_connections_save_independently_and_preserve_credentials(tmp_path):
    from fastapi import FastAPI
    from backend.connections import connection_router
    from backend.worker import Worker
    settings,provider=make(tmp_path,imap_password='private-existing-secret')
    app=FastAPI()
    async def auth():pass
    app.include_router(connection_router(settings,Worker(settings,provider.store),auth,tmp_path/'.env'))
    with TestClient(app) as client:
        assert client.put('/api/connections/weather',json={'weather_enabled':True}).status_code==200
        assert settings.weather_enabled and not settings.holidays_enabled
        assert client.put('/api/connections/holidays',json={'holidays_enabled':True}).status_code==200
        assert settings.imap_password=='private-existing-secret'
        assert client.put('/api/connections/weather',json={'holidays_enabled':False}).status_code==422
        assert settings.holidays_enabled
        assert 'private-existing-secret' not in client.get('/api/connections').text


def test_manual_city_resolution_is_cached_and_maps_german_state(tmp_path,monkeypatch):
    _,provider=make(tmp_path,weather_enabled=True);calls=[]
    async def get(url,params):
        calls.append((url,params));return {'results':[{'name':'Beispielort','country_code':'DE','admin1':'Bayern','latitude':49.153,'longitude':10.547,'timezone':'Europe/Berlin'}]}
    monkeypatch.setattr(provider,'get_json',get)
    first=asyncio.run(provider.locations('Beispielort'))
    second=asyncio.run(provider.locations('Beispielort'))
    assert first==second and len(calls)==1
    assert first[0]['subdivision']=='DE-BY' and first[0]['latitude']==49.15
    assert calls[0][1]['name']=='Beispielort'


def test_outdoor_context_includes_mail_imported_during_current_question(tmp_path,monkeypatch):
    import backend.main as main
    s,_=make(tmp_path,weather_enabled=True,chat_llm_enabled=True)
    app=create_app(s)
    tomorrow=datetime.now(ZoneInfo('Europe/Berlin')).date()+timedelta(days=1)
    async def mail(*args,**kwargs):
        assert 'bauen' in ' '.join(kwargs['queries'])
        app.state.store.commit_item({'id':'fresh-project','version':'1','source':'imap','title':'Schuppen bauen','body':'Kannst du bitte den Schuppen im Garten bauen?','document_date':'2026-08-01'})
        return {'ok':True}
    async def get(*args):return forecast(tomorrow)
    async def no_plan(*args):return None
    async def answer(settings,task,payload):
        assert any(hit['item_id']=='fresh-project' for hit in payload['activity_evidence'])
        return 'Falls du den Schuppen meinst, sieht es morgen trocken aus.'
    monkeypatch.setattr(app.state.worker,'search_mail_context',mail)
    monkeypatch.setattr(app.state.public_data,'get_json',get)
    monkeypatch.setattr(main,'plan_question',no_plan);monkeypatch.setattr(main,'generate',answer)
    with TestClient(app) as client:
        response=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Kann ich morgen draußen arbeiten?','device_location':LOCATION,'share_external':True})
        assert response.status_code==200 and response.json()['external_used']


def test_follow_up_uses_prior_user_question_not_assistant_wording():
    history=[{'role':'user','text':'Kann ich morgen draußen arbeiten?'},{'role':'assistant','text':'Ja, vermutlich.'}]
    assert needs_public_context('Und übermorgen?',history)==(True,True)


def test_location_is_not_requested_for_indoor_sensors_or_unrelated_followups():
    assert needs_public_context('Welche Temperatur meldet das Wohnzimmerthermostat?')==(False,False)
    history=[{'role':'user','text':'Wie wird das Wetter morgen?'}]
    assert needs_public_context('Was wollte Tanja morgen?',history)==(False,False)
    assert needs_public_context('Kann ich morgen den Rasen mähen?')==(True,True)


def test_ambiguous_or_invalid_day_is_not_silently_replaced_by_today(tmp_path,monkeypatch):
    assert target_day('Wetter am Wochenende?',date(2026,11,1)) is None
    assert target_day('Wetter am 31.02.2027?',date(2026,11,1)) is None
    assert target_day('nächsten Montag',date(2026,11,2))==date(2026,11,9)
    _,provider=make(tmp_path,weather_enabled=True)
    result=asyncio.run(provider.context('Wie wird das Wetter nächste Woche?'))
    assert result['date_needed'] and not result['sources']


def test_location_preflight_cannot_read_another_accounts_conversation(tmp_path):
    s,_=make(tmp_path,weather_enabled=True);app=create_app(s)
    owner=app.state.accounts.create('owner@example.org','example-password')
    member=app.state.accounts.create('member@example.org','example-password')
    conversation=app.state.store.save_exchange(None,'private request',{'answer':'private response','sources':[]},owner['id'])
    token,_=app.state.accounts.new_session(member['id'])
    with TestClient(app,base_url='https://testserver') as client:
        client.cookies.set('butler_session',token)
        response=client.post('/api/chat/location-needed',headers={'Origin':'https://testserver'},json={'message':'Wetter morgen?','conversation_id':conversation})
        assert response.status_code==404 and 'private response' not in response.text
