import asyncio
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo
from xml.sax.saxutils import escape
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.icloud import DAV,PersonalSources,account_scope,apple_url,parse_events,parse_contact
from backend.store import Store

ZONE=ZoneInfo('Europe/Berlin')
START=datetime(2026,11,2,tzinfo=ZONE);END=START+timedelta(days=7)
CONTACT='BEGIN:VCARD\r\nVERSION:3.0\r\nUID:person-1\r\nFN:Michael Beispiel\r\nEMAIL:me@example.org\r\nTEL:+490000000\r\nORG:Example Company;Development\r\nTITLE:Engineer\r\nNOTE:private-note-not-needed\r\nEND:VCARD\r\n'
ICS='''BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//test//EN
BEGIN:VEVENT
UID:work-1
DTSTART;TZID=Europe/Berlin:20261102T090000
DTEND;TZID=Europe/Berlin:20261102T100000
RRULE:FREQ=DAILY;COUNT=5
EXDATE;TZID=Europe/Berlin:20261103T090000
SUMMARY:Arbeitstermin
END:VEVENT
BEGIN:VEVENT
UID:work-1
RECURRENCE-ID;TZID=Europe/Berlin:20261104T090000
DTSTART;TZID=Europe/Berlin:20261104T140000
DTEND;TZID=Europe/Berlin:20261104T150000
SUMMARY:Verschoben
END:VEVENT
BEGIN:VEVENT
UID:holiday-1
DTSTART;VALUE=DATE:20261105
DTEND;VALUE=DATE:20261106
SUMMARY:Urlaub
END:VEVENT
BEGIN:VEVENT
UID:cancelled
DTSTART;TZID=Europe/Berlin:20261106T110000
DTEND;TZID=Europe/Berlin:20261106T120000
STATUS:CANCELLED
SUMMARY:Abgesagt
END:VEVENT
END:VCALENDAR
'''


def settings(path,**values):
    return Settings(_env_file=None,data_dir=path,api_token='test',worker_enabled=False,icloud_calendar_enabled=True,icloud_contacts_enabled=True,icloud_calendar_user='me@example.org',icloud_contacts_user='me@example.org',icloud_calendar_password='example-app-password',icloud_contacts_password='example-app-password',**values)


def multistatus(props,href='/item'):
    return f'<d:multistatus xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav" xmlns:a="urn:ietf:params:xml:ns:carddav"><d:response><d:href>{escape(href)}</d:href><d:propstat><d:prop>{props}</d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response></d:multistatus>'


def test_calendar_recurrences_exceptions_cancelled_and_all_day():
    records=parse_events(ICS,{'url':'https://caldav.icloud.com/home/family/','name':'Familie'},START,END,'Europe/Berlin')
    work=[row for row in records if not row['all_day']]
    assert len(work)==4 and not any(row['title']=='Abgesagt' for row in records)
    assert not any(row['start'].startswith('2026-11-03') for row in work)
    moved=next(row for row in work if row['title']=='Verschoben')
    assert moved['start'].startswith('2026-11-04T14:00')
    vacation=next(row for row in records if row['all_day'])
    assert vacation['start'].startswith('2026-11-05T00:00') and vacation['end'].startswith('2026-11-06T00:00')
    tomorrow=START+timedelta(days=4)
    assert not any(row['title']=='Urlaub' for row in parse_events(ICS,{'url':'https://caldav.icloud.com/home/family/','name':'Familie'},tomorrow,tomorrow+timedelta(days=1),'Europe/Berlin'))


def test_read_only_dav_discovery_and_shared_calendar_selection(tmp_path,monkeypatch):
    s=settings(tmp_path,icloud_calendar_names='Familie');calls=[]
    def handle(request):
        calls.append((request.method,str(request.url)))
        assert request.method in ('PROPFIND','REPORT')
        path=request.url.path
        if path=='/':body=multistatus('<d:current-user-principal><d:href>/principal/</d:href></d:current-user-principal>')
        elif path=='/principal/':body=multistatus('<c:calendar-home-set><d:href>/home/</d:href></c:calendar-home-set>')
        elif path=='/home/':body=multistatus('<d:displayname>Familie</d:displayname><d:resourcetype><c:calendar/></d:resourcetype><c:supported-calendar-component-set><c:comp name="VEVENT"/></c:supported-calendar-component-set>','/home/family/')
        else:
            assert b'calendar-query' in request.content and b'time-range' in request.content
            body=multistatus('<c:calendar-data>'+escape(ICS)+'</c:calendar-data>','/home/family/event.ics')
        return httpx.Response(207,content=body.encode())
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    result=asyncio.run(DAV(s,'calendar').read(s,START,END))
    assert result['collections']==['Familie'] and len(result['records'])==5
    assert len(calls)==4


def test_icloud_rejects_credential_redirects_and_xml_entities(tmp_path,monkeypatch):
    s=settings(tmp_path)
    for url in ('http://caldav.icloud.com/','https://caldav.icloud.com.evil.test/','https://evil.test/','https://user:pass@caldav.icloud.com/','https://127.0.0.1/'):
        with pytest.raises(ValueError):apple_url(url,'https://caldav.icloud.com/','calendar')
    original=httpx.AsyncClient;calls=[]
    def redirect(request):
        calls.append(str(request.url));return httpx.Response(302,headers={'location':'https://evil.test/steal'})
    async def run():
        dav=DAV(s,'calendar')
        async with original(transport=httpx.MockTransport(redirect)) as client:
            with pytest.raises(ValueError):await dav.request(client,'PROPFIND',dav.root,'<x/>')
            with pytest.raises(ValueError):await dav.request(client,'DELETE',dav.root,'')
        xml='<!DOCTYPE x [<!ENTITY secret SYSTEM "file:///etc/passwd">]><x>&secret;</x>'
        async with original(transport=httpx.MockTransport(lambda _:httpx.Response(207,content=xml))) as client:
            with pytest.raises(Exception):await dav.request(client,'PROPFIND',dav.root,'<x/>')
    asyncio.run(run())
    assert calls==['https://caldav.icloud.com/']


def test_carddav_contact_parsing_omits_notes_and_updates_company():
    first=parse_contact(CONTACT,'contacts:scope')
    second=parse_contact(CONTACT.replace('Example Company','New Company'),'contacts:scope')
    assert first['id']==second['id'] and first['organization']!=second['organization']
    assert first['emails']==['me@example.org'] and 'private-note-not-needed' not in str(first)


def test_live_profile_changes_and_deletions_without_fixed_employer(tmp_path,monkeypatch):
    s=settings(tmp_path);store=Store(tmp_path);personal=PersonalSources(s,store)
    first=parse_contact(CONTACT,account_scope(s,'contacts'));company=['Example Company'];deleted=[False];calls=[]
    async def read(self,settings,start=None,end=None,zone='Europe/Berlin'):
        calls.append(self.kind)
        return {'records':[] if deleted[0] else [{**first,'organization':company[0]}],'collections':['Kontakte']}
    monkeypatch.setattr(DAV,'read',read)
    initial=asyncio.run(personal.context('Wo arbeite ich?',identity=first['id']))
    company[0]='New Employer'
    changed=asyncio.run(personal.context('Wo arbeite ich?',identity=first['id']))
    assert initial['self_contact']['organization']=='Example Company'
    assert changed['self_contact']['organization']=='New Employer'
    assert len(calls)==2
    deleted[0]=True
    assert asyncio.run(personal.context('Wo arbeite ich?',identity=first['id']))['self_contact'] is None


def test_source_failure_and_account_switch_never_masquerade_as_live(tmp_path,monkeypatch):
    s=settings(tmp_path);store=Store(tmp_path);personal=PersonalSources(s,store)
    async def good(self,*args,**kwargs):return {'records':[],'collections':['Familie']}
    monkeypatch.setattr(DAV,'read',good)
    asyncio.run(personal.refresh('calendar',START,END,force=True))
    async def failed(*args,**kwargs):raise RuntimeError('private password details')
    monkeypatch.setattr(DAV,'read',failed)
    stale=asyncio.run(personal.refresh('calendar',START,END,force=True))
    assert stale['stale'] and 'private password' not in str(personal.status)
    assert asyncio.run(personal.refresh('calendar',START,END))['stale']
    s.icloud_calendar_user='another@example.org'
    assert asyncio.run(personal.refresh('calendar',START,END,force=True)) is None


def test_contact_aliases_are_only_used_for_unique_fresh_person(tmp_path,monkeypatch):
    s=settings(tmp_path);personal=PersonalSources(s,Store(tmp_path))
    row=parse_contact(CONTACT.replace('Michael Beispiel','Tanja Beispiel'),'contacts:scope')
    async def read(*args,**kwargs):return {'records':[row],'collections':['Kontakte']}
    monkeypatch.setattr(DAV,'read',read)
    context=asyncio.run(personal.context('Was wollte Tanja?',sender='Tanja'))
    assert context['sender_aliases']==['me@example.org']
    assert context['self_contact'] is None


def test_identity_selection_is_per_user_and_passwords_remain_write_only(tmp_path,monkeypatch):
    from backend.connections import connection_router
    from backend.worker import Worker
    from fastapi import FastAPI
    s=settings(tmp_path);app=create_app(s);accounts=app.state.accounts
    user=accounts.create('owner@example.org','example-password');other=accounts.create('other@example.org','example-password')
    row=parse_contact(CONTACT,account_scope(s,'contacts'))
    async def read(*args,**kwargs):return {'records':[row],'collections':['Kontakte']}
    monkeypatch.setattr(DAV,'read',read)
    token,_=accounts.new_session(user['id']);second,_=accounts.new_session(other['id'])
    with TestClient(app,base_url='https://testserver') as client:
        client.cookies.set('butler_session',token)
        headers={'Origin':'https://testserver'}
        candidates=client.post('/api/account/contact-candidates',headers=headers,json={'query':'Michael'}).json()
        assert candidates['contacts'][0]['id']==row['id']
        assert client.put('/api/account/identity',headers=headers,json={'contact_id':row['id']}).status_code==200
        assert client.get('/api/account/identity').json()['contact']['name']=='Michael Beispiel'
        client.cookies.set('butler_session',second)
        assert client.get('/api/account/identity').json()['contact'] is None
    isolated=FastAPI()
    async def auth():pass
    isolated.include_router(connection_router(s,Worker(s,Store(tmp_path)),auth,tmp_path/'.env'))
    with TestClient(isolated) as client:
        result=client.get('/api/connections').json()
        assert result['secrets']['icloud_calendar_password'] and 'example-app-password' not in str(result)
        assert client.put('/api/connections/icloud_calendar',json={'icloud_calendar_names':'Familie'}).status_code==200
        assert s.icloud_calendar_password=='example-app-password' and s.icloud_contacts_password=='example-app-password'


def test_carddav_discovery_and_readonly_report(tmp_path,monkeypatch):
    s=settings(tmp_path);calls=[]
    def handle(request):
        calls.append(request.method)
        assert request.url.host=='contacts.icloud.com'
        if request.url.path=='/':body=multistatus('<d:current-user-principal><d:href>/me/</d:href></d:current-user-principal>')
        elif request.url.path=='/me/':body=multistatus('<a:addressbook-home-set><d:href>/books/</d:href></a:addressbook-home-set>')
        elif request.url.path=='/books/':body=multistatus('<d:displayname>Kontakte</d:displayname><d:resourcetype><a:addressbook/></d:resourcetype>','/books/default/')
        else:
            assert b'addressbook-query' in request.content
            body=multistatus('<a:address-data>'+escape(CONTACT)+'</a:address-data>','/books/default/me.vcf')
        return httpx.Response(207,content=body.encode())
    original=httpx.AsyncClient;monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    result=asyncio.run(DAV(s,'contacts').read(s))
    assert result['records'][0]['name']=='Michael Beispiel' and calls==['PROPFIND']*3+['REPORT']


def test_unsupported_dense_recurrence_never_looks_like_an_empty_free_calendar():
    with pytest.raises(ValueError,match='dichte'):
        parse_events(ICS.replace('FREQ=DAILY;COUNT=5','FREQ=SECONDLY;COUNT=99999999'),{'url':'https://caldav.icloud.com/home/','name':'Familie'},START,END,'Europe/Berlin')


def test_chat_combines_calendar_with_past_mail_and_weather(tmp_path,monkeypatch):
    import backend.main as main
    s=settings(tmp_path,weather_enabled=True,chat_llm_enabled=True);app=create_app(s)
    tomorrow=datetime.now(ZONE).date()+timedelta(days=1)
    row={'id':'event-1','title':'Besprechung','description':'','location':'','start':tomorrow.isoformat()+'T09:00:00+01:00','end':tomorrow.isoformat()+'T10:00:00+01:00','all_day':False,'busy':True,'calendar':'Familie'}
    async def read(self,*args,**kwargs):return {'records':[row] if self.kind=='calendar' else [],'collections':['Familie']}
    async def weather(*args,**kwargs):return {'hourly':{'time':[tomorrow.isoformat()+'T12:00'],'temperature_2m':[17],'precipitation_probability':[5],'precipitation':[0],'wind_speed_10m':[5],'wind_gusts_10m':[8]}}
    async def plan(*args):return None
    async def answer(settings,task,payload):
        assert payload['personal_live_context']['calendar']['events'][0]['title']=='Besprechung'
        assert payload['public_context']['weather']['hours']
        assert payload['activity_evidence'][0]['title']=='Hühnerstall'
        assert 'keine festen Arbeitszeiten' in task and 'Widersprüche' in task
        return 'Für den Hühnerstall sieht das Wetter passend aus; von 9 bis 10 Uhr steht allerdings eine Besprechung im Kalender.'
    app.state.store.commit_item({'id':'project','title':'Hühnerstall','body':'Kannst du bitte den Hühnerstall bauen?','source':'imap','version':'1'})
    monkeypatch.setattr(DAV,'read',read);monkeypatch.setattr(app.state.public_data,'get_json',weather)
    monkeypatch.setattr(main,'plan_question',plan);monkeypatch.setattr(main,'generate',answer)
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Kann ich morgen draußen arbeiten?','share_external':True,'device_location':{'latitude':52.52,'longitude':13.4}}).json()
    assert result['external_used'] and {'calendar','weather','imap'}<={row['source'] for row in result['sources']}
    assert not app.state.store.events()


def test_spouse_is_only_resolved_from_explicit_relation_on_confirmed_self_card(tmp_path,monkeypatch):
    s=settings(tmp_path);personal=PersonalSources(s,Store(tmp_path))
    own=parse_contact(CONTACT.replace('END:VCARD','item1.X-ABRELATEDNAMES:Anna Beispiel\r\nitem1.X-ABLabel:_$!<Spouse>!$_\r\nEND:VCARD'),account_scope(s,'contacts'))
    partner=parse_contact(CONTACT.replace('UID:person-1','UID:person-2').replace('Michael Beispiel','Anna Beispiel').replace('me@example.org','anna@example.org'),account_scope(s,'contacts'))
    async def read(*args,**kwargs):return {'records':[own,partner],'collections':['Kontakte']}
    monkeypatch.setattr(DAV,'read',read)
    unconfirmed=asyncio.run(personal.context('Was wollte meine Frau?',sender='meine Frau'))
    assert not unconfirmed.get('sender_aliases')
    confirmed=asyncio.run(personal.context('Was wollte meine Frau?',sender='meine Frau',identity=own['id']))
    assert confirmed['sender_aliases']==['anna@example.org']
    assert 'Explizite' in confirmed['relationship_basis']


def test_declined_participation_is_preserved_without_guessing_personal_availability():
    data=ICS.replace('SUMMARY:Arbeitstermin','ATTENDEE;PARTSTAT=DECLINED:mailto:me@example.org\nSUMMARY:Arbeitstermin')
    records=parse_events(data,{'url':'https://caldav.icloud.com/home/','name':'Familie'},START,END,'Europe/Berlin')
    row=next(row for row in records if row['title']=='Arbeitstermin')
    assert row['attendees']==[{'email':'me@example.org','status':'DECLINED'}]


def test_natural_availability_and_profile_questions_select_live_sources():
    from backend.icloud import wants_calendar,self_profile_question
    assert wants_calendar('Habe ich morgen Zeit?')
    assert wants_calendar('Muss ich morgen arbeiten?')
    assert wants_calendar('Was steht morgen an?')
    assert self_profile_question('Wo bin ich angestellt?')
    assert not wants_calendar('Wo arbeite ich?')
    assert not self_profile_question('Wer ist der Arbeitgeber von Tanja?')


def test_non_dav_success_page_is_not_an_empty_calendar(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _:httpx.Response(200,content='<html>login</html>'))) as client:
            dav=DAV(settings(tmp_path),'calendar')
            with pytest.raises(ValueError,match='DAV-Antwort'):await dav.request(client,'REPORT',dav.root,'<x/>')
    asyncio.run(run())
