import asyncio
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.store import Store
from backend.worker import Worker
from backend.intelligence import local_decision, generate

CONTEXT = {'projects':[{'name':'Wartung','keywords':['Heizung'],'active':True}]}
def item(**kw):
    return dict(id='mail:1', version='1', title='Heizung Rechnung', body='Wartung der Heizung', source='imap', **kw)

def test_filters():
    assert local_decision(item(spam=True),CONTEXT) is None
    ad = item(advertising=True)
    assert local_decision(ad,{'projects':[]}) is None
    assert local_decision(ad,CONTEXT)
    assert local_decision(item(),{'projects':[]})
    assert local_decision(ad,{'projects':[{'name':'x','keywords':['Heiz'],'active':True}]}) is None

def test_privacy_gate():
    assert asyncio.run(generate(Settings(_env_file=None,allow_external_llm=False),'x',{'secret':'private'})) is None

def test_dedup_and_retrieval(tmp_path):
    store = Store(tmp_path)
    worker = Worker(Settings(_env_file=None,data_dir=tmp_path),store)
    asyncio.run(worker.process(item()))
    asyncio.run(worker.process(item()))
    assert len(store.events()) == 1
    assert store.search('Heizung')[0]['item_id']=='mail:1'
    updated = item(); updated['version']='2'; updated['body']='Neue Rechnung'
    asyncio.run(worker.process(updated))
    assert len(store.events())==2
    assert store.search('Wartung')==[]
    assert len(Store(tmp_path).events(pending=True))==2

def test_api(tmp_path):
    app = create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test-secret',worker_enabled=False))
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/context').status_code==401
        headers={'Authorization':'Bearer test-secret'}
        assert client.put('/api/context',json=CONTEXT,headers=headers).status_code==200
        assert client.get('/api/context',headers=headers).json()['projects'][0]['name']=='Wartung'
        assert client.put('/api/context',json={'projects':[{'name':'x','keywords':['']} ]},headers=headers).status_code==422
        assert client.post('/api/chat',json={'message':'Heizung','share_external':True},headers=headers).json()['external_used'] is False
        assert client.post('/api/webhooks/messenger').status_code==403

def test_source_failure_isolation(tmp_path, monkeypatch):
    import backend.worker as module
    async def broken(_):
        raise RuntimeError('offline')
    monkeypatch.setattr(module,'read_paperless',broken)
    monkeypatch.setattr(module,'read_mail',lambda *_: [item()])
    store = Store(tmp_path)
    worker = Worker(Settings(_env_file=None,data_dir=tmp_path),store)
    asyncio.run(worker.tick())
    assert worker.status['sources']['paperless']['error']
    assert worker.status['sources']['imap']['errors']==0
    assert len(store.events())==1
    assert not worker.status['running']

def test_connection_settings_write_only(tmp_path):
    from fastapi import FastAPI
    from backend.connections import connection_router
    settings=Settings(_env_file=None,data_dir=tmp_path,api_token='test-secret',worker_enabled=False)
    worker=Worker(settings,Store(tmp_path))
    async def auth():
        pass
    app=FastAPI()
    env=tmp_path/'.env'
    app.include_router(connection_router(settings,worker,auth,env))
    with TestClient(app) as client:
        assert client.put('/api/connections',json={'llm_provider':'gemini','llm_model':'gemini-test','llm_api_key':'private-key','allow_external_llm':True}).status_code==422
        assert not env.exists()
        value={'llm_provider':'gemini','llm_model':'gemini-test','llm_api_key':'private-key','allow_external_llm':True,'gemini_paid_confirmed':True}
        assert client.put('/api/connections',json=value).status_code==200
        result=client.get('/api/connections')
        assert 'private-key' not in result.text
        assert result.json()['secrets']['llm_api_key']
        assert 'private-key' in env.read_text()
        del value['llm_api_key']
        assert client.put('/api/connections',json=value).status_code==200
        assert settings.llm_api_key=='private-key'
        assert client.put('/api/connections',json={'api_token':'bad'}).status_code==422
        assert env.stat().st_mode & 0o777 == 0o600

def test_independent_connections_and_provider_switch(tmp_path):
    from fastapi import FastAPI
    from backend.connections import connection_router
    s=Settings(_env_file=None,data_dir=tmp_path, llm_api_key='existing-key',llm_model='existing-model', imap_host='mail.example')
    app=FastAPI()
    async def auth(): pass
    app.include_router(connection_router(s,Worker(s,Store(tmp_path)),auth,tmp_path/'.env'))
    with TestClient(app) as client:
        assert client.put('/api/connections/paperless',json={'paperless_url':'https://paperless.example','paperless_token':'document-key'}).status_code==200
        assert s.imap_host=='mail.example'
        assert s.llm_api_key=='existing-key' and s.llm_model=='existing-model'
        assert client.put('/api/connections/imap',json={'paperless_token':'wrong-group'}).status_code==422
        assert s.paperless_token=='document-key'
        assert client.put('/api/connections/llm',json={'llm_provider':'gemini'}).status_code==422
        assert s.llm_provider=='openai'
        assert client.put('/api/connections/llm',json={'llm_provider':'gemini','llm_api_key':'new-key'}).status_code==200
        assert s.llm_model=='' and not s.allow_external_llm

def test_models_pagination_and_secret_handling(tmp_path,monkeypatch):
    from fastapi import FastAPI
    from backend.connections import connection_router
    import httpx
    from backend import connections
    s=Settings(_env_file=None,data_dir=tmp_path,llm_provider='gemini',llm_api_key='stored-secret')
    seen=[]
    def handle(request):
        seen.append(request)
        assert 'stored-secret' not in str(request.url)
        if request.url.host=='api.openai.com':
            return httpx.Response(401,json={'error':'secret-must-not-leak'})
        assert request.headers['x-goog-api-key']=='draft-secret'
        if request.url.params.get('pageToken')=='page2':
            return httpx.Response(200,json={'models':[{'name':'models/gemini-b','supportedGenerationMethods':['generateContent']}]})
        return httpx.Response(200,json={'models':[
            {'name':'models/gemini-a','displayName':'Gemini A','supportedGenerationMethods':['generateContent']},
            {'name':'models/embedding','supportedGenerationMethods':['embedContent']}], 'nextPageToken':'page2'})
    original=httpx.AsyncClient
    monkeypatch.setattr(connections.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    app=FastAPI()
    async def auth(): pass
    app.include_router(connection_router(s,Worker(s,Store(tmp_path)),auth,tmp_path/'.env'))
    with TestClient(app) as client:
        r=client.post('/api/connections/llm/models',json={'provider':'gemini','api_key':'draft-secret'})
        assert r.status_code==200
        assert [m['id'] for m in r.json()['models']]==['gemini-a','gemini-b']
        assert len(seen)==2
        assert s.llm_api_key=='stored-secret'
        assert not (tmp_path/'.env').exists()
        assert client.post('/api/connections/llm/models',json={'provider':'openai'}).status_code==422
        r=client.post('/api/connections/llm/models',json={'provider':'openai','api_key':'new-key'})
        assert r.status_code==502
        assert 'secret-must-not-leak' not in r.text and 'new-key' not in r.text

def test_persistent_session_and_csrf(tmp_path):
    s=Settings(_env_file=None,data_dir=tmp_path,api_token='session-test',worker_enabled=False)
    app=create_app(s);app.state.accounts.create('owner@example.org','a-secure-example-passphrase')
    with TestClient(app,base_url='https://testserver') as client:
        headers={'Origin':'https://testserver'}
        assert client.post('/api/session',json={'email':'owner@example.org','password':'wrong'},headers=headers).status_code==401
        r=client.post('/api/session',json={'email':'owner@example.org','password':'a-secure-example-passphrase'},headers=headers)
        assert r.status_code==200
        cookie=r.headers['set-cookie'].lower()
        assert 'httponly' in cookie and 'samesite=strict' in cookie and 'max-age=2592000' in cookie and 'secure' in cookie
        assert 'session-test' not in cookie
        assert client.get('/api/connections').status_code==200
        assert client.put('/api/context',json={'projects':[]},headers={'Origin':'https://evil.example'}).status_code==403
        assert client.put('/api/context',json={'projects':[]}).status_code==403
        assert client.put('/api/context',json={'projects':[]},headers=headers).status_code==200
        with TestClient(create_app(s),base_url='https://testserver') as restarted:
            restarted.cookies.update(client.cookies)
            assert restarted.get('/api/session').status_code==200
        stolen=client.cookies.get('butler_session')
        assert client.delete('/api/session',headers=headers).status_code==200
        assert client.get('/api/session').status_code==401
        client.cookies.set('butler_session',stolen)
        assert client.get('/api/session').status_code==401


def test_iobroker_status_semantics_context_only(tmp_path):
    from backend.smarthome import classify
    assert classify('light.power',{'val':False})=='value'
    assert classify('device.connected',{'val':False})=='offline'
    assert classify('device.UNREACH',{'val':True})=='offline'
    assert classify('device.connected',{'val':True,'q':64})=='unknown'
    assert classify('device.connected',{'val':None})=='unknown'
    store=Store(tmp_path)
    state={'id':'device.connected','name':'Gerät','value':True,'status':'online','source_ts':1}
    store.save_smart_snapshot('home',[state],'first')
    assert store.events()==[]
    store.save_smart_snapshot('home',[{**state,'status':'offline','value':False}],'second')
    store.save_smart_snapshot('home',[{**state,'status':'offline','value':False}],'third')
    assert store.events()==[]
    store.save_smart_snapshot('home',[],'fourth')
    assert store.smart_snapshot('home')['states'][0]['status']=='missing'
    store.save_smart_snapshot('home',[state],'fifth')
    assert store.events()==[]
    assert Store(tmp_path).smart_snapshot('home')['checked']=='fifth'

def test_iobroker_read_only_and_failure_preserves_snapshot(tmp_path,monkeypatch):
    import httpx
    from backend import smarthome
    from backend.smarthome import read_iobroker,scope
    s=Settings(_env_file=None,data_dir=tmp_path,iobroker_url='http://home:8087',iobroker_enabled=True,iobroker_patterns='*.connected,*.power')
    calls=[]
    fail=False
    def handler(request):
        calls.append(request)
        assert request.method=='GET' and request.url.path=='/states'
        assert set(request.url.params)=={'pattern'}
        if fail: return httpx.Response(503)
        if request.url.params['pattern']=='*.connected':
            return httpx.Response(200,json={'device.connected':{'val':True,'ts':123},'private.token':{'val':'do-not-keep'},'empty.connected':None})
        return httpx.Response(200,json={'light.power':{'val':False}})
    original=httpx.AsyncClient
    monkeypatch.setattr(smarthome.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    result=asyncio.run(read_iobroker(s))
    assert len(result)==3 and len(calls)==2
    assert next(s for s in result if s['id']=='empty.connected')['status']=='unknown'
    assert 'do-not-keep' not in str(result)
    store=Store(tmp_path);worker=Worker(s,store)
    asyncio.run(worker.poll_iobroker())
    previous=store.smart_snapshot(scope(s))
    fail=True
    asyncio.run(worker.poll_iobroker())
    assert worker.status['sources']['iobroker']['error']
    assert store.smart_snapshot(scope(s))==previous
    assert store.events()==[]

def test_iobroker_config_validation(tmp_path):
    from pydantic import ValidationError
    import pytest
    with pytest.raises(ValidationError):
        Settings(_env_file=None,iobroker_url='http://home:8087/?value=true')
    with pytest.raises(ValidationError):
        Settings(_env_file=None,iobroker_url='http://user:pass@home:8087')
    with pytest.raises(ValidationError):
        Settings(_env_file=None,iobroker_patterns='')

def test_local_setup_replaces_automatic_login(tmp_path):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='local-test',worker_enabled=False))
    with TestClient(app,base_url='http://127.0.0.1:8010',client=('127.0.0.1',54321)) as client:
        assert client.get('/api/connections').status_code==401
        assert client.post('/api/session/local').status_code==404
        value={'email':'owner@example.org','password':'a-secure-example-passphrase'}
        assert client.post('/api/auth/bootstrap',json=value,headers={'Origin':'https://evil.example'}).status_code==403
        headers={'Origin':'http://127.0.0.1:8010','Sec-Fetch-Site':'same-origin'}
        assert client.post('/api/auth/bootstrap',json=value,headers={**headers,'Sec-Fetch-Site':'cross-site'}).status_code==403
        assert client.post('/api/auth/bootstrap',json=value,headers=headers).status_code==200
        assert client.get('/api/connections').status_code==200
        assert client.post('/api/auth/bootstrap',json=value,headers=headers).status_code==409
    with TestClient(app,base_url='http://127.0.0.1:8010',client=('192.168.1.42',54321)) as remote:
        assert remote.post('/api/auth/bootstrap',json=value,headers=headers).status_code==403


def test_question_retrieval_ignores_function_words_and_deduplicates(tmp_path):
    store=Store(tmp_path)
    for id,title,body in [('jobs','Jobs','Wann beginnt die Wartung der IT-Systeme?'),('news','Newsletter','Die letzte Verhandlung war gestern.'),('report','Heizungswartung','Wartungsbericht der Heizung vom 12.09.2025.\n'*100)]:
        store.commit_item({'id':id,'version':'1','title':title,'body':body,'source':'test'})
    hits=store.search('wann war die letzte Wartung der Heizung?')
    assert [h['item_id'] for h in hits]==['report']
    assert store.search('wann war die?')==[]
    assert store.search('" OR *')==[]

def test_local_answer_does_not_invent_maintenance_date():
    from backend.retrieval import local_answer
    answer=local_answer('Wann war die letzte Wartung der Heizung?',[],['Paperless'])
    assert 'Wartungsdatum' in answer and 'nicht' in answer
    assert len(answer)<160 and 'Paperless' not in answer
    answer=local_answer('Wann war die Wartung?',[{'title':'Rechnung','body':'Link https://tracking.example/'+'x'*500}],[])
    assert 'https://' not in answer and len(answer)<700

def test_paperless_proxy_pagination_keeps_https_and_credentials_on_origin(monkeypatch):
    from backend import sources
    import httpx
    calls=[]
    def handle(request):
        calls.append(request)
        assert request.url.scheme=='https' and request.url.host=='paperless.example'
        assert request.headers['authorization']=='Token private'
        if request.url.params.get('page')=='2':
            return httpx.Response(200,json={'results':[{'id':2,'title':'Wartung','content':'Heizung gewartet'}],'next':None})
        return httpx.Response(200,json={'results':[{'id':1,'title':'Beleg','content':'x'}],'next':'http://internal:8000/api/documents/?page=2'})
    original=httpx.AsyncClient
    monkeypatch.setattr(sources.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handle),**kw))
    docs=asyncio.run(sources.read_paperless(Settings(_env_file=None,paperless_url='https://paperless.example',paperless_token='private')))
    assert len(docs)==2 and len(calls)==2

def test_iobroker_is_ai_context_not_a_raw_chat_list(tmp_path,monkeypatch):
    import backend.main as module
    from backend.smarthome import scope
    s=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,iobroker_url='http://home:8087',allow_external_llm=True)
    app=create_app(s)
    app.state.store.save_smart_snapshot(scope(s),[{'id':'lamp.connected','name':'Lampe','value':False,'status':'offline','source_ts':1}],'2026-10-31T12:00:00Z')
    captured=[]
    async def fake_generate(settings,task,payload):
        captured.append(payload)
        return 'Die Lampe meldet sich offline.'
    monkeypatch.setattr(module,'generate',fake_generate)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'}
        r=client.post('/api/chat',headers=headers,json={'message':'Welche Geräte sind offline?','share_external':True})
        assert r.json()['answer']=='Die Lampe meldet sich offline.'
        assert captured[0]['smart_home']['states'][0]['id']=='lamp.connected'
        assert all(h['item_id']!='iobroker:snapshot' for h in captured[0]['sources'])
        r=client.post('/api/chat',headers=headers,json={'message':'Welche Geräte sind offline?','share_external':False})
        assert 'lamp.connected' not in r.json()['answer']
        assert client.get('/api/events',headers=headers).json()==[]

def test_retrieval_includes_mail_and_paperless_across_chunks(tmp_path):
    store=Store(tmp_path)
    for n in range(7):
        store.commit_item({'id':f'doc:{n}','version':'1','title':'Heizungswartung Rechnung','body':'Heizung Wartung abgeschlossen.','source':'paperless'})
    store.commit_item({'id':'mail:old','version':'1','title':'Terminbestätigung','body':'Heizung '+('x '*1500)+' Wartungsauftrag bestätigt.','source':'imap'})
    hits=store.search('Wann war die letzte Wartung der Heizung?')
    assert len(hits)==5
    assert {'imap','paperless'}=={h['source'] for h in hits}
    assert sum(h['source']=='imap' for h in hits)==1

def test_historical_mail_query_has_no_date_limit_and_is_read_only(monkeypatch):
    import email.message
    from backend import sources
    msg=email.message.EmailMessage();msg['Subject']='Heizung Wartung';msg.set_content('Wartung unserer Heizung im Jahr 2020.')
    commands=[]
    class FakeIMAP:
        reject_text=False
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def login(self,*args): pass
        def select(self,folder,readonly):
            assert readonly
            return 'OK',[]
        def response(self,key): return key,[b'1']
        def uid(self,command,*args):
            commands.append((command,args))
            if command=='search':
                if self.reject_text and any('TEXT' in str(arg) for arg in args):return 'NO',[b'full text search not supported']
                return 'OK',[b'99']
            assert args[1]=='(BODY.PEEK[])'
            return 'OK',[(b'99',msg.as_bytes())]
    monkeypatch.setattr(sources.imaplib,'IMAP4_SSL',FakeIMAP)
    settings=Settings(_env_file=None,imap_host='mail.example',imap_user='me')
    result=sources.search_mail(settings,[['heiz','therme'],['wart','service']])
    assert result['total']==1 and len(result['items'])==1
    search=commands[0][1]
    assert not any('SINCE' in str(part) for part in search)
    assert '(OR TEXT "heiz" TEXT "therme")' in search
    assert '(OR TEXT "wart" TEXT "service")' in search
    FakeIMAP.reject_text=True
    commands.clear()
    fallback=sources.search_mail(settings,[['heiz','therme'],['wart','service']])
    assert fallback['subject_only'] and len(fallback['items'])==1
    assert any('SUBJECT' in args for cmd,args in commands if cmd=='search')

def test_historical_context_does_not_call_llm(tmp_path,monkeypatch):
    import backend.worker as module
    settings=Settings(_env_file=None,data_dir=tmp_path,imap_host='mail.example',allow_external_llm=True)
    old=item();old['id']='historical';old['source']='imap'
    monkeypatch.setattr(module,'search_mail',lambda *args:{'items':[old],'total':1,'limited':False})
    store=Store(tmp_path);worker=Worker(settings,store)
    state=asyncio.run(worker.search_mail_context('Wann war die Wartung der Heizung?'))
    assert state['ok'] and store.unchanged(old)
    assert store.events()==[]

def test_settings_save_does_not_wait_for_background_scan(tmp_path):
    from fastapi import FastAPI
    from backend.connections import connection_router
    import httpx
    async def run():
        s=Settings(_env_file=None,data_dir=tmp_path,ntfy_url='https://old.example/topic',imap_password='keep-me')
        worker=Worker(s,Store(tmp_path))
        app=FastAPI()
        async def auth(): pass
        env=tmp_path/'.env'
        app.include_router(connection_router(s,worker,auth,env))
        async with worker.lock:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
                response=await asyncio.wait_for(client.put('/api/connections/ntfy',json={'ntfy_url':'https://new.example/topic'}),timeout=1)
                assert response.status_code==200
        assert s.ntfy_url=='https://new.example/topic'
        assert s.imap_password=='keep-me'
        assert 'https://new.example/topic' in env.read_text()
    asyncio.run(run())

def test_notifications_are_batched_and_rate_limited(tmp_path,monkeypatch):
    import httpx
    from backend import worker as module
    store=Store(tmp_path)
    calls=[]
    original=httpx.AsyncClient
    def send(request):
        calls.append(request)
        return httpx.Response(200,json={})
    monkeypatch.setattr(module.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(send),**kw))
    w=Worker(Settings(_env_file=None,data_dir=tmp_path,ntfy_url='https://ntfy.example/topic',ntfy_mode='important'),store)
    for index,title in enumerate(['Rechnung','Newsletter','Mahnung','Sicherheitswarnung']):
        value=item();value['id']=str(index);value['title']=title
        store.commit_item(value,'Neuer Vorgang')
    asyncio.run(w.flush_notifications())
    assert len(calls)==1
    assert b'2 wichtige neue' in calls[0].content
    assert not store.pending_notifications()
    value=item();value['id']='new';value['title']='Mahnung'
    store.commit_item(value,'Wichtig')
    asyncio.run(w.flush_notifications())
    assert len(calls)==1 and len(store.pending_notifications())==1
    # Reconstructing the worker preserves the cooldown.
    asyncio.run(Worker(w.s,store).flush_notifications())
    assert len(calls)==1
    w.s.ntfy_mode='off'
    asyncio.run(w.flush_notifications())
    assert not store.pending_notifications()

def test_initial_import_keeps_findings_without_notifications(tmp_path,monkeypatch):
    from backend import worker as module
    async def docs(_):return [item()]
    monkeypatch.setattr(module,'read_paperless',docs)
    monkeypatch.setattr(module,'read_mail',lambda *_:[])
    store=Store(tmp_path);w=Worker(Settings(_env_file=None,data_dir=tmp_path),store)
    asyncio.run(w.tick())
    assert len(store.events())==1
    assert store.pending_notifications()==[]

def test_short_maintenance_answer_uses_execution_week_not_invoice_date():
    from backend.retrieval import local_answer
    hits=[{'title':'Rechnung Heizungswartung','body':'Datum: 18.04.2024\nAuf Grund unserer geleisteten Lieferung/Leistung\nHeizungswartung Solarfocus\nAusführungstermin: KW 16 / 2024','source':'paperless'},
          {'title':'Wartungsprotokoll','body':'Wartungsarbeiten vom 10.09.2020 durchgeführt.','source':'paperless'},
          {'title':'Terminbestätigung','body':'Geplante Wartung am 01.12.2026.','source':'imap'}]
    answer=local_answer('Wann war die letzte Wartung der Heizung?',hits,[])
    assert 'KW 16/2024' in answer and '15.04.' in answer and '21.04.2024' in answer
    assert '18.04.' not in answer and '01.12.' not in answer
    assert len(answer)<140

def test_newsletters_and_jobs_are_not_maintenance_evidence(tmp_path):
    store=Store(tmp_path)
    for id,title in [('newsletter','Beispiel Newsletter'),('jobs','KFZ-Meister gesucht · 30 Jobs')]:
        store.commit_item({'id':id,'version':'1','title':title,'body':'Heizung Wartung Service','source':'imap'})
    assert store.search('Wann war die letzte Wartung der Heizung?')==[]

def test_new_maintenance_invoice_can_outweigh_old_exact_report():
    from backend.retrieval import maintenance_answer
    hits=[{'title':'Heizungswartung 2024','body':'Wartungsarbeiten vom 15.04.2024 durchgeführt.'},
          {'title':'Solarfocus Service Rechnung Wartung 2026','body':'Wartung Pelletkessel, Servicepauschale.','document_date':'2026-09-08'}]
    answer=maintenance_answer('Wann war die letzte Wartung der Heizung?',hits)
    assert 'wahrscheinlich' in answer and 'September 2026' in answer
    assert '2024' not in answer

def test_explicit_checklist_date_beats_invoice_and_ocr_dates():
    from backend.retrieval import maintenance_answer
    hit={'title':'Solarfocus Service Rechnung Wartung 2026','document_date':'2026-09-08',
         'body':'Lorsch, 08.09.2032\nServiceeinsatz gemäß Wartungscheckliste vom 20.08.2026 durchgeführt von Hrn. Beispiel.'}
    answer=maintenance_answer('Wann war die letzte Wartung der Heizung?',[hit])
    assert '20.08.2026' in answer and 'wahrscheinlich' not in answer
    assert '2032' not in answer and '08.09.' not in answer

def test_recent_brand_based_evidence_is_retrieved_and_ranked_first(tmp_path):
    store=Store(tmp_path)
    for n in range(8):
        store.commit_item({'id':f'old:{n}','title':'Heizungswartung Rechnung','body':'Heizung Wartungsarbeiten vom 10.09.2020 durchgeführt.','source':'paperless','version':'1','document_date':'2020-09-15'})
    latest={'id':'new','title':'Solarfocus Service Rechnung Wartung 2026','body':'Serviceeinsatz gemäß Wartungscheckliste vom 20.08.2026 durchgeführt.','source':'paperless','version':'1','document_date':'2026-09-08'}
    store.commit_item(latest)
    hits=store.search('Wann war die letzte Wartung der Heizung?')
    assert hits[0]['item_id']=='new' and hits[0]['document_date']=='2026-09-08'
    assert len(hits)==5
    # Metadata updates remain possible even if OCR text and version stay unchanged.
    store.set_item_metadata({**latest,'document_date':'2026-09-09'})
    assert store.search('Heizungswartung')[0]['document_date']=='2026-09-09'

def test_sender_question_understanding_and_chronological_retrieval(tmp_path):
    from backend.retrieval import question_sender,sender_answer
    assert question_sender('Was wollte Anna zuletzt von mir?')=='Anna'
    assert question_sender('was wollte anna zuletzt von mir?')=='anna'
    assert question_sender('Was wollte Anna Müller zuletzt?')=='Anna Müller'
    assert question_sender('Neueste Mail von Peter?')=='Peter'
    assert question_sender('Wann war die letzte Wartung der Heizung?') is None
    store=Store(tmp_path)
    for id,date,body in [('old','2026-10-21T10:00:00+00:00','Bitte den Flyer ändern.'),('new','2026-10-28T10:00:00+00:00','Auf deiner Seite ist das falsche Logo. Das bitte schon mal ausbessern.\nVon: Andere Person\nBitte den alten Flyer ändern.')]:
        store.commit_item({'id':id,'version':'1','title':'Beispielprojekt','body':body,'source':'imap','sender_name':'Anna Beispiel','sender_address':'anna@example.org','sent_at':date})
    store.commit_item({'id':'mention','version':'1','title':'Anna','body':'Zitat von Anna','source':'imap','sender_name':'Jemand anders','sender_address':'other@example.org','sent_at':'2026-10-30T10:00:00+00:00'})
    hits=store.sender_messages('Anna')
    assert [h['item_id'] for h in hits]==['new','old']
    answer=sender_answer('Anna',hits)
    assert 'Logo' in answer and 'ausbessern' in answer
    assert 'alten Flyer' not in answer

def test_ai_failure_does_not_prevent_local_mail_indexing(tmp_path,monkeypatch):
    import backend.worker as module
    async def broken(*args,**kwargs):raise RuntimeError('provider unavailable')
    monkeypatch.setattr(module,'generate',broken)
    store=Store(tmp_path);w=Worker(Settings(_env_file=None,data_dir=tmp_path,allow_external_llm=True),store)
    value=item();value.update(sender_name='Anna',sender_address='anna@example.org',sent_at='2026-10-28T10:00:00+00:00')
    asyncio.run(w.process(value))
    assert store.unchanged(value) and store.sender_messages('Anna')
    assert store.next_analysis() is not None
    asyncio.run(w.analysis_one())
    assert w.status['analysis_fallbacks']==1

def test_latest_purchase_uses_order_confirmations_not_ads_or_shipping(tmp_path):
    from backend.retrieval import is_purchase_question,purchase_answer
    store=Store(tmp_path)
    values=[('old','Bestellt: Filament','2026-10-30T18:00:00+00:00','* Filament\nSumme\n10.00 EUR'),('new','Bestellt: ESP32 und Sensor','2026-10-31T09:00:00+00:00','* ESP32 Entwicklungsboard\n Menge: 1\n* Wasserdichter Ultraschallsensor\nSumme\n20.58 EUR'),('ad','Jetzt kaufen: Sonderangebot','2026-10-31T10:00:00+00:00','Werbung'),('shipping','Versendet: Alter Kauf','2026-10-31T11:00:00+00:00','Versand')]
    for id,title,date,body in values:
        store.commit_item({'id':id,'title':title,'body':body,'version':'1','source':'imap','sender_address':'shop@example.org','sent_at':date})
    assert is_purchase_question('Was habe ich zuletzt gekauft?')
    hits=store.recent_purchases()
    assert hits[0]['item_id']=='new' and len(hits)==2
    answer=purchase_answer(hits)
    assert 'ESP32' in answer and 'Ultraschallsensor' in answer and '20,58' in answer

def test_provider_rate_limit_is_cached_without_repeated_requests(monkeypatch):
    import httpx
    from backend import intelligence
    calls=[]
    def reply(request):
        calls.append(request)
        return httpx.Response(429,json={'error':{'message':'quota'}})
    original=httpx.AsyncClient
    monkeypatch.setattr(intelligence.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(reply),**kw))
    s=Settings(_env_file=None,allow_external_llm=True,llm_api_key='limited-test-unique',llm_model='test-model')
    for _ in range(2):
        try:asyncio.run(intelligence.generate(s,'Test',{}))
        except intelligence.ProviderLimited:pass
    assert len(calls)==1 and intelligence.provider_is_limited(s)

def test_chat_purchase_survives_provider_limit(tmp_path,monkeypatch):
    from backend import main as module
    from backend.intelligence import ProviderLimited
    s=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,allow_external_llm=True)
    app=create_app(s)
    app.state.store.commit_item({'id':'buy','title':'Bestellt: Boards','body':'* ESP32 Set\n* Sensor\nSumme\n20.58 EUR','source':'imap','version':'1','sent_at':'2026-10-31T09:00:00+00:00','sender_address':'shop@example.org'})
    async def limited(*args):raise ProviderLimited('limit')
    monkeypatch.setattr(module,'generate',limited)
    with TestClient(app) as client:
        r=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Was habe ich zuletzt gekauft?','share_external':True})
        assert r.status_code==200
        result=r.json()
        assert 'ESP32' in result['answer'] and '20,58' in result['answer']
        assert result['sources'] and not result['external_used']
        assert any('429' in message for message in result['diagnostics'])

def test_account_activity_separates_login_signup_and_unconfirmed_codes(tmp_path):
    from backend.retrieval import account_answer,is_account_question
    store=Store(tmp_path)
    values=[('login','New sign-in to your Example account','Your account was accessed.','2026-10-31T14:34:05+00:00'),('signup','Herzlich Willkommen bei Shop','Wir begrüßen Sie als neuen Kunden. Kundenlogin ist bereit.','2026-10-17T04:23:00+00:00'),('code','Your temporary ChatGPT login code','123456','2026-10-31T15:00:00+00:00'),('verify','Bei AWS anmelden – E-Mail-Verifizierung','Diese E-Mail ist bereits einem Konto zugeordnet.','2026-10-31T15:01:00+00:00')]
    for id,title,body,date in values:
        store.commit_item({'id':id,'title':title,'body':body,'source':'imap','version':'1','sent_at':date,'sender_name':'Example','sender_address':'noreply@example.org'})
    assert is_account_question('Wo habe ich mich zuletzt angemeldet?')
    hits=store.recent_account_activity()
    assert {h['item_id'] for h in hits}=={'login','signup'}
    answer=account_answer('Wo habe ich mich zuletzt angemeldet?',hits)
    assert 'Example' in answer and 'Shop' in answer
    assert '123456' not in answer and 'KI' not in answer
    assert 'Shop' in account_answer('Wo habe ich mich zuletzt registriert?',hits)

def test_html_mail_text_excludes_scripts_styles_and_markup():
    from backend.retrieval import message_text
    html='<html><head><style>secret-css</style></head><body><p>New sign-in</p><script>evil()</script><p>Example</p></body></html>'
    text=message_text(html)
    assert 'New sign-in' in text and 'Example' in text
    assert 'secret-css' not in text and 'evil' not in text and '<p>' not in text

def test_chat_ai_works_when_background_ai_is_off(tmp_path,monkeypatch):
    from backend import main as module
    from backend.planning import RetrievalPlan
    s=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,allow_external_llm=False,chat_llm_enabled=True)
    app=create_app(s)
    app.state.store.commit_item({'id':'purchase','title':'Bestellt: Test','body':'* Sensor','source':'imap','version':'1'})
    called=[]
    async def plan(settings,question):
        assert settings.allow_external_llm
        called.append('plan')
        return RetrievalPlan(intent='purchases',recent=True)
    async def answer(settings,task,payload):
        assert settings.allow_external_llm and payload['sources']
        called.append('answer')
        return 'Zuletzt hast du einen Sensor bestellt.'
    monkeypatch.setattr(module,'plan_question',plan)
    monkeypatch.setattr(module,'generate',answer)
    with TestClient(app) as client:
        r=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Was habe ich zuletzt gekauft?','share_external':True})
        result=r.json()
        assert result['external_used'] and result['answer_mode']=='ai' and not result['query_planned_by_ai']
        assert called==['answer']
        assert not s.allow_external_llm
        status=client.get('/api/status',headers={'Authorization':'Bearer test'}).json()
        assert status['external_llm'] is True

def test_explicit_chat_opt_out_is_respected_with_background_enabled(tmp_path,monkeypatch):
    from backend import main as module
    s=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,allow_external_llm=True,chat_llm_enabled=False)
    app=create_app(s)
    async def forbidden(*args):raise AssertionError('must not call provider')
    monkeypatch.setattr(module,'plan_question',forbidden)
    monkeypatch.setattr(module,'generate',forbidden)
    with TestClient(app) as client:
        result=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Was habe ich zuletzt gekauft?','share_external':True}).json()
        assert result['answer_mode']=='local' and not result['external_used']

def test_ntfy_history_is_read_only_deduplicated_and_isolates_topic_errors(tmp_path,monkeypatch):
    import httpx,json
    from backend import notifications
    calls=[]
    def handler(request):
        calls.append(request)
        assert request.method=='GET' and request.url.params['poll']=='1'
        if request.url.path=='/denied/json':return httpx.Response(403)
        event={'id':'door-event','event':'message','time':1690000000,'title':'Klingel','message':'Jemand hat geklingelt.'}
        return httpx.Response(200,content=json.dumps(event)+'\n',headers={'content-type':'application/x-ndjson'})
    original=httpx.AsyncClient
    monkeypatch.setattr(notifications.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    store=Store(tmp_path)
    s=Settings(_env_file=None,data_dir=tmp_path,ntfy_url='https://ntfy.example/door',ntfy_history_urls='https://ntfy.example/door\nhttps://ntfy.example/denied')
    first=asyncio.run(notifications.sync_ntfy(s,store))
    assert first['imported']==1 and first['errors']==1
    again=asyncio.run(notifications.sync_ntfy(s,store))
    assert again['imported']==0
    assert any(r.url.params['since']=='door-event' for r in calls)
    assert store.search('Klingel')[0]['source']=='ntfy'
    assert store.events()==[] and store.pending_notifications()==[]
    assert store.coverage_summary()['ntfy_topics_failed']==1

def test_archive_reads_all_folders_historical_messages_and_leaves_recent_to_worker(tmp_path,monkeypatch):
    from backend import archive
    import email.message
    msg=email.message.EmailMessage();msg['Subject']='Archivbeleg';msg['From']='Anna <anna@example.org>';msg['Date']='Mon, 01 Jan 2024 12:00:00 +0000';msg.set_content('Historischer Inhalt.')
    fetched=[]
    class Mailbox:
        def __init__(self,*args,**kwargs):self.folder=''
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def login(self,*args):pass
        def list(self):return 'OK',[b'(\\Noselect) "." "root"',b'(\\HasNoChildren) "." "INBOX"',b'(\\HasNoChildren) "." "Archiv 2024"']
        def select(self,name,readonly):assert readonly;self.folder=name.strip('"');return 'OK',[]
        def response(self,name):return name,[b'1']
        def uid(self,command,*args):
            if command=='search':
                if args[1]=='ALL':return 'OK',[b'1 2' if self.folder=='INBOX' else b'3']
                return 'OK',[b'2' if self.folder=='INBOX' else b'']
            assert args[1]=='(BODY.PEEK[])';fetched.append(args[0]);return 'OK',[(b'message',msg.as_bytes())]
    monkeypatch.setattr(archive.imaplib,'IMAP4_SSL',Mailbox)
    s=Settings(_env_file=None,data_dir=tmp_path,imap_host='mail.example',imap_user='me')
    store=Store(tmp_path);result=archive.backfill_batch(s,store)
    assert set(fetched)=={b'1',b'3'}
    assert result['total']==3 and result['indexed']==2 and not result['complete']
    assert len(store.mail_ids())==2 and not store.events()
    assert store.sender_messages('Anna')

def test_smart_state_discovery_uses_names_and_does_not_expose_native_config(tmp_path,monkeypatch):
    import httpx
    from backend import smarthome
    def handler(request):
        assert request.method=='GET'
        if request.url.path=='/objects':
            return httpx.Response(200,json={'sensor.1':{'type':'state','common':{'name':'Türklingel','read':True,'role':'button'},'native':{'password':'never-expose'}},'private.token':{'type':'state','common':{'name':'Türklingel'}}})
        assert request.url.path=='/get/sensor.1'
        return httpx.Response(200,json={'val':True,'ts':1690000000000})
    original=httpx.AsyncClient
    monkeypatch.setattr(smarthome.httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    s=Settings(_env_file=None,data_dir=tmp_path,iobroker_url='http://home:8087')
    states,inventory=asyncio.run(smarthome.relevant_states(s,['Klingel']))
    assert len(states)==1 and states[0]['name']=='Türklingel'
    assert 'never-expose' not in str(inventory) and 'native' not in str(inventory)
    store=Store(tmp_path)
    store.record_smart_states('scope',states,'2026-10-31T10:00:00Z')
    store.record_smart_states('scope',states,'2026-10-31T10:01:00Z')
    assert len(store.smart_history_context('scope',['Klingel']))==1

def test_question_can_combine_mail_and_ntfy_with_coverage(tmp_path,monkeypatch):
    from backend import main as module
    from backend.planning import RetrievalPlan
    s=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True)
    app=create_app(s);store=app.state.store
    store.commit_item({'id':'mail:delivery','title':'Paket XYZ','body':'Paket XYZ heute in Zustellung.','source':'imap','version':'1'})
    store.commit_item({'id':'ntfy:bell','title':'Klingel','body':'Es hat um 12:30 geklingelt.','source':'ntfy','version':'1','document_date':'2026-10-31T11:30:00+00:00'})
    async def plan(*args):return RetrievalPlan(intent='events',queries=['Paket XYZ','Klingel'],sources=['email','ntfy'],recent=True)
    async def no_network():pass
    async def answer(settings,task,payload):
        assert {'imap','ntfy'}<={hit['source'] for hit in payload['sources']}
        assert 'source_coverage' in payload and payload['display_timezone']=='Europe/Berlin'
        assert 'allein beweist keine Paketzustellung' in task
        return 'Wahrscheinlich war ein Zustellversuch; eine bestätigte Übergabe ist noch nicht belegt.'
    monkeypatch.setattr(module,'plan_question',plan)
    monkeypatch.setattr(module,'generate',answer)
    app.state.worker.poll_ntfy=no_network
    with TestClient(app) as client:
        r=client.post('/api/chat',headers={'Authorization':'Bearer test'},json={'message':'Ist Paket XYZ schon da?','share_external':True})
        assert r.status_code==200 and r.json()['external_used']
        assert {'imap','ntfy'}<={source.get('source') for source in r.json()['sources']}
        assert not store.events()

def test_mobile_account_login_replaces_pairing(tmp_path):
    settings=Settings(_env_file=None,data_dir=tmp_path,api_token='test-secret',worker_enabled=False,public_origin='https://butler.example')
    app=create_app(settings);app.state.accounts.create('owner@example.org','a-secure-example-passphrase')
    with TestClient(app,base_url='https://butler.example') as phone:
        assert phone.post('/api/session/pair',json={'code':'1234567890'}).status_code==404
        assert phone.post('/api/session/pairing').status_code==404
        assert phone.get('/api/status').status_code==401
        payload={'email':'owner@example.org','password':'a-secure-example-passphrase'}
        assert phone.post('/api/session',json=payload,headers={'Origin':'https://evil.example'}).status_code==403
        assert phone.post('/api/session',json=payload,headers={'Origin':'https://butler.example'}).status_code==200
        assert phone.get('/api/status').status_code==200
        assert phone.get('/api/status').headers['cache-control']=='no-store'
    with TestClient(app,base_url='http://butler.example') as insecure:
        assert insecure.get('/').status_code==403


def test_account_login_attempt_limit(tmp_path):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test-secret',worker_enabled=False))
    with TestClient(app,base_url='https://testserver') as client:
        for _ in range(10):
            assert client.post('/api/session',json={'email':'unknown@example.org','password':'wrong'},headers={'Origin':'https://testserver'}).status_code==401
        assert client.post('/api/session',json={'email':'unknown@example.org','password':'wrong'},headers={'Origin':'https://testserver'}).status_code==429


def test_local_speech_endpoint_private_and_limits(tmp_path,monkeypatch):
    import backend.speech as speech
    received=[]
    def transcribe(audio,path):
        received.append(audio)
        return 'Wann hat jemand geklingelt?'
    monkeypatch.setattr(speech,'transcribe_audio',transcribe)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    with TestClient(app) as client:
        assert client.post('/api/speech/transcribe',content=b'audio',headers={'Content-Type':'application/octet-stream'}).status_code==401
        headers={'Authorization':'Bearer test','Content-Type':'application/octet-stream'}
        assert client.get('/api/speech',headers=headers).json()=={'local':True,'ready':False,'max_seconds':60,'tts_ready':False,'tts_voice':'Thorsten · lokal / neuronal'}
        assert client.post('/api/speech/transcribe',content=b'',headers=headers).status_code==422
        assert client.post('/api/speech/transcribe',content=b'x'*(4*1024*1024+1),headers=headers).status_code==413
        result=client.post('/api/speech/transcribe',content=b'audio',headers=headers)
        assert result.json()=={'text':'Wann hat jemand geklingelt?','local':True}
        assert received==[b'audio']

def test_conversation_history_persists_locally_and_requires_auth(tmp_path):
    settings=Settings(_env_file=None,data_dir=tmp_path,api_token='test-secret',worker_enabled=False)
    store=Store(tmp_path)
    result={'answer':'Am 12. Mai.','sources':[{'id':'example','title':'Wartungsbeleg','excerpt':'Beispieltext'}],'diagnostics':[],'external_used':False}
    id=store.save_exchange(None,'Wann war die Wartung?',result)
    store.save_exchange(id,'In welchem Jahr?',{**result,'answer':'2025.'})
    assert len(Store(tmp_path).conversation(id)['messages'])==4
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/conversations').status_code==401
        headers={'Authorization':'Bearer test-secret'}
        assert client.get('/api/conversations',headers=headers).json()[0]['id']==id
        restored=client.get('/api/conversations/'+id,headers=headers).json()
        assert restored['messages'][1]['payload']['sources']==result['sources']
        assert client.get('/api/conversations/missing',headers=headers).status_code==404
        assert client.post('/api/chat',headers=headers,json={'message':'x','conversation_id':'../bad'}).status_code==422
        assert client.post('/api/chat',headers=headers,json={'message':'x','conversation_id':'0'*32}).status_code==404


def test_webapp_worker_is_public_but_private_data_not_in_cache(tmp_path):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test-secret',worker_enabled=False))
    with TestClient(app) as client:
        response=client.get('/sw.js')
        assert response.status_code==200
        assert response.headers['service-worker-allowed']=='/'
        assert "url.pathname.startsWith('/api/')" in response.text
        manifest=client.get('/static/manifest.webmanifest').json()
        assert manifest['display']=='standalone'
        assert client.get('/static/offline.html').status_code==200

def test_follow_up_passes_recent_conversation_to_planner_and_answer(tmp_path,monkeypatch):
    import backend.main as module
    from backend.planning import RetrievalPlan
    planned=[];payloads=[]
    async def plan(settings,question):
        planned.append(question)
        return RetrievalPlan(sources=[])
    async def answer(settings,task,payload):
        payloads.append(payload)
        return 'Eine kurze Testantwort.'
    monkeypatch.setattr(module,'plan_question',plan)
    monkeypatch.setattr(module,'generate',answer)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True))
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'}
        first=client.post('/api/chat',headers=headers,json={'message':'Wann war die Beispielwartung?','share_external':True}).json()
        second=client.post('/api/chat',headers=headers,json={'message':'In welchem Jahr?','share_external':True,'conversation_id':first['conversation_id']}).json()
        assert second['external_used'] is True
        assert payloads[0]['conversation']==[]
        assert payloads[1]['conversation'][0]['text']=='Wann war die Beispielwartung?'
        assert len(payloads[1]['conversation'])==2
        assert 'Aktuelle Frage: In welchem Jahr?' in planned[1]
        assert second['conversation_id']==first['conversation_id']


def test_iobroker_connection_validation_preserves_saved_configuration(tmp_path):
    from backend.connections import connection_router
    settings=Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,iobroker_url='http://home:8087')
    # Use a temporary settings file rather than the real deployment credentials.
    from fastapi import FastAPI
    isolated=FastAPI()
    async def auth(): pass
    isolated.include_router(connection_router(settings,Worker(settings,Store(tmp_path)),auth,tmp_path/'.env'))
    with TestClient(isolated) as client:
        response=client.put('/api/connections/iobroker',json={'iobroker_url':'192.168.1.126:8087'})
        assert response.status_code==422
        assert response.json()['detail'][0]['loc'][-1]=='iobroker_url'
        assert settings.iobroker_url=='http://home:8087'
        response=client.put('/api/connections/iobroker',json={'iobroker_patterns':','.join('pattern'+str(n) for n in range(11))})
        assert response.status_code==422
        assert response.json()['detail'][0]['loc'][-1]=='iobroker_patterns'
        assert client.put('/api/connections/iobroker',json={'iobroker_url':'http://home:8087/','iobroker_api':'simple','iobroker_patterns':'*.connected,*.UNREACH','iobroker_enabled':True}).status_code==200
        assert settings.iobroker_url=='http://home:8087'
        assert settings.iobroker_enabled


def test_initial_document_progress_persists_and_retries_errors(tmp_path,monkeypatch):
    import hashlib
    from backend import worker as module
    settings=Settings(_env_file=None,data_dir=tmp_path,worker_enabled=False,paperless_url='https://docs.example',api_token='test')
    store=Store(tmp_path);worker=Worker(settings,store)
    key='initial-paperless:'+hashlib.sha256(settings.paperless_url.encode()).hexdigest()[:16]
    async def docs(_):
        assert store.sync_state(key)['phase']=='fetching'
        return [{**item(),'id':'doc-1'},{**item(),'id':'doc-2'}]
    monkeypatch.setattr(module,'read_paperless',docs)
    monkeypatch.setattr(module,'read_mail',lambda *_:[])
    original=worker.process
    async def fail_one(value,**kwargs):
        assert store.sync_state(key)['total']==2
        if value['id']=='doc-2':raise RuntimeError('retry')
        await original(value,**kwargs)
    monkeypatch.setattr(worker,'process',fail_one)
    asyncio.run(worker.tick())
    state=store.sync_state(key)
    assert state['indexed']==1 and state['total']==2 and not state['complete'] and state['phase']=='error'
    monkeypatch.setattr(worker,'process',original)
    asyncio.run(worker.tick())
    assert store.sync_state(key)['complete']
    with TestClient(create_app(settings)) as client:
        sync=client.get('/api/sync',headers={'Authorization':'Bearer test'}).json()
        assert sync['initial_import']['paperless']['complete']
        assert sync['initial_import']['paperless']['indexed']==2
    # Another Paperless instance must not inherit the previous instance's completion.
    settings.paperless_url='https://other.example'
    with TestClient(create_app(settings)) as client:
        assert client.get('/api/sync',headers={'Authorization':'Bearer test'}).json()['initial_import']['paperless']=={}
