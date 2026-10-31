import asyncio
import os
import ipaddress
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager, suppress
import secrets
import time
from typing import Annotated
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pathlib import Path
from pydantic import BaseModel, Field
from .config import Settings
from .store import Store
from .accounts import Accounts
from .worker import Worker
from .intelligence import generate, ProviderLimited, provider_is_limited
from .planning import plan_question
from .smarthome import scope
from .lighting import is_current_lighting_question, light_answer
from .retrieval import local_answer, maintenance_answer, evidence_recency, question_sender, is_purchase_question, is_account_question
import json
import re

class Project(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    keywords: list[Annotated[str, Field(min_length=2,max_length=100)]] = Field(max_length=30)
    active: bool = True
    notes: str = Field(default='', max_length=2000)

class Context(BaseModel):
    projects: list[Project] = Field(max_length=30)

class Chat(BaseModel):
    message: str = Field(min_length=1,max_length=4000)
    share_external: bool = False
    conversation_id: str | None = Field(default=None,pattern=r'^[a-f0-9]{32}$')


def create_app(settings=None):
    s = settings or Settings(_env_file=os.environ.get('BUTLER_ENV_FILE','.env'))
    store = Store(s.data_dir)
    worker = Worker(s,store)
    accounts=Accounts(store)
    app_password_slots=asyncio.Semaphore(2)
    @asynccontextmanager
    async def lifespan(app):
        if not s.api_token:
            raise RuntimeError('Set API_TOKEN in .env before starting')
        task = asyncio.create_task(worker.run()) if s.worker_enabled else None
        archive_task=asyncio.create_task(worker.archive_loop()) if s.worker_enabled and s.imap_archive_enabled else None
        yield
        for running in (task,archive_task):
            if running:
                running.cancel()
                with suppress(asyncio.CancelledError):
                    await running
    app = FastAPI(title='Lokaler KI-Butler', lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    @app.exception_handler(RequestValidationError)
    async def private_validation_error(request,exception):
        return JSONResponse({'detail':[{key:error[key] for key in ('loc','msg','type') if key in error} for error in exception.errors()]},status_code=422)
    from urllib.parse import urlsplit
    public=urlsplit(s.public_origin)
    if s.public_origin and (public.scheme!='https' or not public.hostname or public.username or public.password or public.path not in ('','/') or public.query or public.fragment):
        raise RuntimeError('PUBLIC_ORIGIN muss eine reine HTTPS-Origin sein')
    allowed_hosts=['localhost','127.0.0.1','testserver']+([public.hostname] if public.hostname else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    from .security import SecurityMiddleware
    app.state.store, app.state.worker = store, worker
    @app.middleware('http')
    async def private_responses(request,call_next):
        if public.hostname and request.url.hostname==public.hostname and request.url.scheme!='https':
            return JSONResponse({'detail':'Handy-Zugriff erfordert HTTPS'},status_code=403)
        return await call_next(request)
    app.add_middleware(SecurityMiddleware)
    cookie_name='butler_session'
    app.state.accounts=accounts

    def local_browser(request):
        try:return request.url.hostname in ('localhost','127.0.0.1') and ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError,AttributeError):return False

    def same_origin(request):
        if request.headers.get('origin')!=str(request.base_url).rstrip('/') or request.headers.get('sec-fetch-site','same-origin')!='same-origin':
            raise HTTPException(403,'Anfrage stammt nicht von dieser Butler-Oberfläche')
        if request.url.scheme!='https' and not local_browser(request):
            raise HTTPException(403,'Die Anmeldung erfordert HTTPS')

    async def auth(request:Request,authorization:str=Header(default='')):
        request.state.user=None
        if s.api_token and authorization and secrets.compare_digest(authorization,'Bearer '+s.api_token):return
        user=accounts.session(request.cookies.get(cookie_name,''))
        if not user:raise HTTPException(401,'Bitte mit E-Mail-Adresse und Passwort anmelden')
        request.state.user=user
        if request.method not in ('GET','HEAD','OPTIONS'):same_origin(request)

    async def admin_auth(request:Request,authorization:str=Header(default='')):
        await auth(request,authorization)
        if request.state.user and request.state.user['role']!='admin':raise HTTPException(403,'Nur für Administratoren')

    def user_owner(request):
        return request.state.user['id'] if request.state.user else None

    def session_response(request,user,remember=True):
        token,lifetime=accounts.new_session(user['id'],remember)
        response=JSONResponse({'authenticated':True,'user':user})
        response.set_cookie(cookie_name,token,max_age=lifetime if remember else None,httponly=True,samesite='strict',secure=request.url.scheme=='https',path='/api')
        return response

    class Credentials(BaseModel):
        email:str=Field(min_length=3,max_length=254)
        password:str=Field(min_length=1,max_length=1024)
        remember:bool=True

    class NewAccount(BaseModel):
        email:str=Field(min_length=3,max_length=254)
        password:str=Field(min_length=12,max_length=1024)
        role:Annotated[str,Field(pattern=r'^(admin|member)$')]='member'

    class PasswordChange(BaseModel):
        current_password:str=Field(min_length=1,max_length=1024)
        new_password:str=Field(min_length=12,max_length=1024)

    class AccountState(BaseModel):
        active:bool

    @app.get('/api/auth/config')
    async def auth_config(request:Request):
        return {'configured':accounts.count()>0,'bootstrap_local':local_browser(request)}

    @app.post('/api/auth/bootstrap')
    async def bootstrap(value:NewAccount,request:Request):
        same_origin(request)
        if not local_browser(request):raise HTTPException(403,'Erstes Konto im LXC mit aibutler user add EMAIL anlegen')
        if accounts.count():raise HTTPException(409,'Die Ersteinrichtung ist bereits abgeschlossen.')
        if app_password_slots.locked():raise HTTPException(429,'Bitte kurz warten')
        async with app_password_slots:
            try:user=await asyncio.to_thread(accounts.create,value.email,value.password,'admin',True)
            except ValueError as exc:raise HTTPException(409,str(exc))
        return session_response(request,user)

    @app.post('/api/session')
    async def login(value:Credentials,request:Request):
        same_origin(request)
        if app_password_slots.locked():raise HTTPException(429,'Zu viele gleichzeitige Anmeldungen. Bitte kurz warten.')
        async with app_password_slots:
            try:user=await asyncio.to_thread(accounts.authenticate,value.email,value.password,request.client.host)
            except ValueError as exc:raise HTTPException(429 if '15 Minuten' in str(exc) else 422,str(exc))
        if not user:raise HTTPException(401,'E-Mail-Adresse oder Passwort ist falsch')
        return session_response(request,user,value.remember)

    @app.get('/api/session',dependencies=[Depends(auth)])
    async def session_status(request:Request):
        return {'authenticated':True,'user':request.state.user}

    @app.delete('/api/session',dependencies=[Depends(auth)])
    async def logout(request:Request):
        accounts.revoke(request.cookies.get(cookie_name,''))
        response=JSONResponse({'authenticated':False})
        response.delete_cookie(cookie_name,path='/api',httponly=True,samesite='strict',secure=request.url.scheme=='https')
        return response

    @app.post('/api/account/password',dependencies=[Depends(auth)])
    async def change_password(value:PasswordChange,request:Request):
        user=request.state.user
        if not user:raise HTTPException(403,'Mit dem Benutzerkonto anmelden')
        if app_password_slots.locked():raise HTTPException(429,'Bitte kurz warten')
        async with app_password_slots:
            try:
                verified=await asyncio.to_thread(accounts.authenticate,user['email'],value.current_password,request.client.host)
                if not verified:raise HTTPException(401,'Aktuelles Passwort ist falsch')
                await asyncio.to_thread(accounts.set_password,user['id'],value.new_password)
            except ValueError as exc:raise HTTPException(429 if '15 Minuten' in str(exc) else 422,str(exc))
        return session_response(request,user)

    @app.get('/api/accounts',dependencies=[Depends(admin_auth)])
    async def list_accounts():return accounts.list()

    @app.post('/api/accounts',dependencies=[Depends(admin_auth)])
    async def create_account(value:NewAccount):
        if app_password_slots.locked():raise HTTPException(429,'Bitte kurz warten')
        async with app_password_slots:
            try:return await asyncio.to_thread(accounts.create,value.email,value.password,value.role)
            except ValueError as exc:raise HTTPException(409,str(exc))

    @app.patch('/api/accounts/{user_id}',dependencies=[Depends(admin_auth)])
    async def account_state(user_id:str,value:AccountState,request:Request):
        if request.state.user and request.state.user['id']==user_id and not value.active:raise HTTPException(409,'Das eigene Konto kann hier nicht gesperrt werden')
        try:accounts.set_active(user_id,value.active)
        except ValueError as exc:raise HTTPException(409,str(exc))
        return accounts.by_id(user_id)

    from .connections import connection_router
    app.include_router(connection_router(s,worker,admin_auth,env_path=Path(os.environ.get('BUTLER_ENV_FILE','.env'))))
    protected = [Depends(auth)]
    @app.get('/healthz')
    async def health():
        return {'status':'ok'}

    @app.get('/api/status',dependencies=protected)
    async def status():
        return {**worker.status,'external_llm':s.chat_llm_enabled if s.chat_llm_enabled is not None else s.allow_external_llm,'worker_enabled':s.worker_enabled,'llm_limited':provider_is_limited(s),'configured_sources':{'imap':bool(s.imap_host),'paperless':bool(s.paperless_url),'ntfy':bool(s.ntfy_history_urls or s.ntfy_url),'iobroker':bool(s.iobroker_url)}}
    @app.get('/api/sync',dependencies=protected)
    async def sync_status():
        import hashlib
        from .notifications import history_urls
        overview=store.sync_overview()
        account=hashlib.sha256((s.imap_host+'/'+s.imap_user).encode()).hexdigest()[:16]
        paperless_key='initial-paperless:'+hashlib.sha256(s.paperless_url.rstrip('/').encode()).hexdigest()[:16]
        topic_keys={'ntfy:'+hashlib.sha256(url.encode()).hexdigest()[:20] for url in history_urls(s)}
        overview['initial_import']={
            'mail': [state for state in overview['states'] if state['key'].startswith('mail-archive:'+account+':')],
            'paperless':store.sync_state(paperless_key),
            'ntfy': [state for state in overview['states'] if state['key'] in topic_keys],
            'ntfy_topics':len(topic_keys),
            'mail_enabled':s.imap_archive_enabled,'ntfy_enabled':s.ntfy_history_enabled,
        }
        return overview

    @app.get('/api/context',dependencies=protected)
    async def context():
        return store.context()
    @app.put('/api/context',dependencies=[Depends(admin_auth)])
    async def update_context(value: Context):
        store.save_context(value.model_dump())
        return value
    @app.get('/api/events',dependencies=protected)
    async def events():
        return store.events()
    @app.post('/api/scan',dependencies=[Depends(admin_auth)])
    async def scan():
        if worker.lock.locked():
            raise HTTPException(409,'Prüfung läuft bereits')
        await worker.tick()
        return worker.status
    @app.get('/api/conversations',dependencies=protected)
    async def conversations(request:Request):
        return store.conversations(user_owner(request))

    @app.get('/api/conversations/{conversation_id}',dependencies=protected)
    async def conversation(conversation_id:str,request:Request):
        result=store.conversation(conversation_id,user_owner(request))
        if result is None:raise HTTPException(404,'Gespräch nicht gefunden')
        return result

    chat_slots=asyncio.Semaphore(2)
    async def chat_capacity():
        if chat_slots.locked():raise HTTPException(429,'Der Butler bearbeitet gerade andere Fragen. Bitte kurz warten.')
        async with chat_slots:yield

    @app.post('/api/chat',dependencies=[*protected,Depends(chat_capacity)])
    async def chat(value: Chat,request:Request):
        owner_id=user_owner(request)
        previous=store.conversation(value.conversation_id,owner_id) if value.conversation_id else None
        if value.conversation_id and previous is None:raise HTTPException(404,'Gespräch nicht gefunden')
        history=[{'role':message['role'],'text':message['text'][:1600]} for message in (previous['messages'][-6:] if previous else [])]
        if is_current_lighting_question(value.message):
            lighting=await worker.lighting_context(value.message)
            sources=[]
            if lighting.get('fresh'):
                rows=[state['name']+' · '+('an' if state['on'] is True else 'aus' if state['on'] is False else 'Zustand unklar') for state in lighting['lights']]
                sources=[{'id':'iobroker:lighting','source':'smart_home','title':'Direkt abgefragte Lichtzustände','excerpt':'\n'.join(rows) or 'Keine eindeutig zugeordneten Lichtaktoren gefunden.','document_date':lighting['checked'],'url':None}]
            diagnostics=list(lighting.get('diagnostics',[]))
            diagnostics.append('Direkte, rein lesende ioBroker-Abfrage. Angezeigt wird der zuletzt vom Gerät gemeldete Zustand; kein KI-Anbieter wurde für diese Frage aufgerufen.')
            if lighting.get('truncated'):diagnostics.append('Die Abfrage wurde auf 300 Licht-Schaltpunkte begrenzt; weitere Lichtpunkte können fehlen.')
            result={'answer':light_answer(value.message,lighting),'sources':sources,'diagnostics':diagnostics,'external_used':False,'answer_mode':'live' if lighting.get('fresh') else 'local','query_kind':'lighting','ai_issue':None,'query_planned_by_ai':False}
            result['conversation_id']=store.save_exchange(value.conversation_id,value.message,result,owner_id)
            return result
        chat_enabled=s.chat_llm_enabled if s.chat_llm_enabled is not None else s.allow_external_llm
        interactive=bool(value.share_external and chat_enabled)
        chat_settings=s.model_copy(update={'allow_external_llm':bool(chat_enabled)})
        plan=None
        if interactive:
            try:
                planning_question=value.message if not history else 'Bisheriger Gesprächskontext (Daten): '+json.dumps(history,ensure_ascii=False)+'\nAktuelle Frage: '+value.message
                plan=await plan_question(chat_settings,planning_question)
            except Exception:
                pass  # Keep data retrieval available; mark a failed answer explicitly below.
        selected=set(plan.sources) if plan else {'email','paperless'}
        sender=(plan.sender if plan and plan.intent=='person' else None) or question_sender(value.message)
        queries=plan.queries if plan else []
        async def skip_mail():return None
        mail_task=worker.search_mail_context(value.message,sender_hint=sender,queries=queries) if 'email' in selected or not plan else skip_mail()
        tasks=[mail_task,worker.search_document_context(queries) if 'paperless' in selected else asyncio.sleep(0),worker.poll_ntfy() if 'ntfy' in selected else asyncio.sleep(0)]
        source_results=await asyncio.gather(*tasks,return_exceptions=True)
        mail_search=source_results[0] if isinstance(source_results[0],dict) else None
        if plan:
            if plan.intent=='person' and sender:
                hits=store.sender_messages(sender)
            elif plan.intent=='purchases':
                hits=store.recent_purchases()
            elif plan.intent=='account':
                hits=store.recent_account_activity()
            else:
                hits=[];seen=set()
                for query in plan.queries or [value.message]:
                    allowed=[{'email':'imap','paperless':'paperless','ntfy':'ntfy'}.get(source,source) for source in selected if source!='smart_home']
                    candidates=store.search(query,source_filter=allowed)
                    if not candidates:candidates=store.search(query,strict=False,source_filter=allowed)
                    for hit in candidates:
                        if hit['item_id'] not in seen:seen.add(hit['item_id']);hits.append(hit)
                if plan.recent:hits.sort(key=evidence_recency,reverse=True)
                hits=hits[:6]
        else:
            hits = store.sender_messages(sender) if sender else store.recent_purchases() if is_purchase_question(value.message) else store.recent_account_activity() if is_account_question(value.message) else store.search(value.message)
        # Intent-specific helpers provide candidates; additional planned sources remain searchable.
        if plan:
            seen={hit['item_id'] for hit in hits}
            extra_allowed=[{'email':'imap','paperless':'paperless','ntfy':'ntfy'}.get(source,source) for source in selected if source!='smart_home']
            for query in plan.queries:
                for hit in store.search(query,strict=False,source_filter=extra_allowed):
                    if hit['item_id'] not in seen:seen.add(hit['item_id']);hits.append(hit)
            # Reserve at least one matching result from each requested source.
            groups={}
            for hit in hits:groups.setdefault(hit.get('source','local'),[]).append(hit)
            hits=[]
            for group in groups.values():
                if plan.recent:group.sort(key=evidence_recency,reverse=True)
            for index in range(8):
                for group in groups.values():
                    if index<len(group) and len(hits)<8:hits.append(group[index])
        extras=await worker.relevant_smart_states(plan.smart_queries or plan.queries) if plan and 'smart_home' in selected else []
        snapshot=store.smart_snapshot(scope(s)) if s.iobroker_url else {'states':[],'checked':None}
        if extras:
            states={state['id']:state for state in snapshot['states']};states.update({state['id']:state for state in extras});snapshot['states']=list(states.values())
        smart_queries=plan.smart_queries if plan else []
        words=set(re.findall(r'\w{3,}',value.message.lower()))-{'der','die','das','ist','sind','was','wie','mir','den','ein','eine','und','mit'}
        for query in smart_queries:words.update(re.findall(r'\w{3,}',query.lower()))
        generic=bool(re.search(r'iobroker|smart.?home|gerät|geraet|offline|online|erreichbar',value.message,re.I))
        extra_ids={state['id'] for state in extras}
        smart=extras+[state for state in snapshot['states'] if state['id'] not in extra_ids and ((generic and not smart_queries) or words.intersection(re.findall(r'\w{3,}',(state['name']+' '+state['id']).lower())))]
        if re.search(r'offline|nicht erreichbar',value.message,re.I):
            smart=[state for state in smart if state['status']=='offline']
        smart_context=None
        if smart:
            smart_context={'checked':snapshot['checked'],'automatic':s.iobroker_enabled,
                'fetch_error':bool(worker.status['sources'].get('iobroker',{}).get('error')),
                'matching_count':len(smart),'states':smart[:30],
                'note':'Letzter gespeicherter Stand, keine Echtzeitgarantie. Nutze passende Werte zur Beantwortung, gib keine rohe Datenpunktliste oder JSON aus. Fehlende Datenpunkte beweisen keine physische Abwesenheit.'}
        answer = None
        provider_issue=None
        if interactive:
            try:
                answer = await generate(chat_settings,'Beantworte die Frage direkt und natürlich, normalerweise in ein bis zwei kurzen Sätzen. Nenne zuerst das Ergebnis. Ordne Quellen nach Bedeutung und Aktualität ein. Unterscheide ausdrücklich belegte Tatsachen und plausible Schlussfolgerungen; kennzeichne eine Vermutung kurz mit wahrscheinlich. Nutze bei E-Mails die aktuelle Nachricht statt älterer zitierter Korrespondenz. Verbinde bei Bedarf E-Mails, Dokumente, ntfy-Ereignisse und passende Smart-Home-Zustände anhand von Datum, Uhrzeit und konkreten Entitäten. Ein Klingelereignis allein beweist keine Paketzustellung, kann zusammen mit Zustellhinweisen aber eine wahrscheinliche Schlussfolgerung stützen. Eine fehlende historische Aufzeichnung beweist nicht, dass nichts passiert ist. Beachte den noch laufenden Archivimport und die begrenzte ntfy-Aufbewahrung. Bei mehrdeutigen Begriffen kläre die zwei naheliegenden Bedeutungen knapp. Keine Fundstellenlisten, Quellen-IDs, Rohzitate, Suchdiagnostik, ungefragten Rechts-/Vertragsbelehrungen oder allgemeinen Sicherheitshinweise. Belege werden separat angezeigt. Erkläre ausführlicher nur auf ausdrücklichen Wunsch.',{'question':value.message,'conversation':history,'current_date':datetime.now(ZoneInfo('Europe/Berlin')).date().isoformat(),'context':store.context(),'sources':hits,'smart_home':smart_context,'mail_search':mail_search,'source_coverage':store.coverage_summary(),'smart_home_changes':store.smart_history_context(scope(s),smart_queries or [value.message]),'display_timezone':'Europe/Berlin','current_time':datetime.now(ZoneInfo('Europe/Berlin')).isoformat()})
            except ProviderLimited:
                provider_issue='Der KI-Anbieter begrenzt gerade Anfragen (HTTP 429). Die Antwort wurde aus lokalen Belegen erstellt.'
            except Exception:
                provider_issue='Die KI-Auswertung ist gerade nicht verfügbar. Lokale Belege wurden trotzdem geprüft.'
        unavailable=[]
        for name,label,configured in [('paperless','Paperless',bool(s.paperless_url)),('imap','E-Mail',bool(s.imap_host))]:
            state=worker.status['sources'].get(name,{})
            if configured and (state.get('error') or not state):
                unavailable.append(label)
        local=local_answer(value.message,hits,unavailable)
        if provider_issue and 'KI-Auswertung einschalten' in local:
            local='Die passenden Unterlagen sind vorhanden. Die KI-Auswertung ist gerade nicht verfügbar.'
        if provider_issue and not hits and not smart_context:
            local='Das kann ich gerade nicht zuverlässig aus deinen Daten beantworten. Meine KI-Auswertung ist vorübergehend nicht verfügbar.'
        diagnostics=[provider_issue] if provider_issue else []
        if unavailable:
            diagnostics.append('Aktueller Abruf unvollständig: '+', '.join(unavailable))
        if mail_search:
            if not mail_search['ok']:
                diagnostics.append('Historische Mail-Suche fehlgeschlagen; lokaler Bestand wurde durchsucht.')
            if mail_search.get('subject_only'):
                diagnostics.append('Der Mailserver unterstützt nur Betreffsuche. Andere Ordner und Nachrichten ohne passende Betreffwörter können fehlen.')
            if mail_search.get('limited'):
                diagnostics.append('Die Postfachsuche ist auf '+('5 neueste Absender-Nachrichten' if mail_search.get('sender_search') else '100 Kandidaten')+' begrenzt.')
        if 'ntfy' in selected:
            history_status=worker.status['sources'].get('ntfy_history',{})
            if history_status.get('error') or history_status.get('errors'):
                diagnostics.append('Ein Teil des ntfy-Verlaufs ist nicht lesbar; fehlende Meldungen sind kein Beweis, dass nichts passiert ist.')
            if history_status.get('truncated'):
                diagnostics.append('Der ntfy-Server hat den rückwirkenden Verlauf gekürzt.')
        coverage=store.coverage_summary()
        if coverage['mail_archive_started'] and not coverage['mail_archive_complete']:
            diagnostics.append('Das vollständige E-Mail-Archiv wird noch lokal eingelesen. Aktuell fehlen '+str(coverage['mail_archive_remaining'])+' Nachrichten im Index.')
        if smart_context and not hits:
            local='Die Smart-Home-Daten sind vorhanden. Die KI-Auswertung ist gerade nicht verfügbar.' if provider_issue else 'Passende Smart-Home-Zustände sind als Kontext vorhanden. Aktiviere die Chat-KI in den Einstellungen für eine formulierte Auswertung.'
        sources=[]
        for hit in hits:
            source={'id':hit['item_id'],'title':hit['title'],'source':hit.get('source','local'),'excerpt':hit['body'],'document_date':hit.get('document_date'),'url':None}
            if hit.get('source')=='paperless' and hit['item_id'].rsplit(':',1)[-1].isdigit():
                source['url']=s.paperless_url.rstrip('/')+'/documents/'+hit['item_id'].rsplit(':',1)[-1]+'/details'
            sources.append(source)
        if smart_context:
            sources.append({'id':'iobroker:snapshot','source':'smart_home','title':'ioBroker-Kontext','excerpt':'Passende Zustände wurden im Hintergrund berücksichtigt.'})
        # A precise locally extracted execution date/period remains available without cloud access.
        if not answer:
            answer=local
        result={'answer':answer,'sources':sources,'diagnostics':diagnostics,'external_used':bool(interactive and not provider_issue),'answer_mode':'ai' if interactive and not provider_issue else 'local','ai_issue':provider_issue,'query_planned_by_ai':plan is not None}
        result['conversation_id']=store.save_exchange(value.conversation_id,value.message,result,owner_id)
        return result
    speech_lock=asyncio.Lock()
    @app.get('/api/speech',dependencies=[Depends(auth)])
    async def speech_status():
        return {'local':True,'ready':s.speech_enabled and (s.data_dir/'models/whisper-base/model.bin').is_file(),'max_seconds':60}

    @app.post('/api/speech/transcribe',dependencies=[Depends(auth)])
    async def speech_transcribe(request:Request):
        if not s.speech_enabled:raise HTTPException(503,'Lokale Spracherkennung ist in dieser Installation deaktiviert')
        if speech_lock.locked():
            raise HTTPException(429,'Spracherkennung ist gerade beschäftigt')
        if request.headers.get('content-type','').split(';')[0]!='application/octet-stream':
            raise HTTPException(415,'Audio als application/octet-stream senden')
        chunks=[];size=0
        async for chunk in request.stream():
            size+=len(chunk)
            if size>4*1024*1024:
                raise HTTPException(413,'Aufnahme ist zu groß (maximal 4 MB)')
            chunks.append(chunk)
        if not size:raise HTTPException(422,'Aufnahme ist leer')
        from .speech import transcribe_audio
        async with speech_lock:
            try:
                text=await asyncio.to_thread(transcribe_audio,b''.join(chunks),s.data_dir/'models/whisper-base')
            except RuntimeError as exc:raise HTTPException(503,str(exc))
            except Exception:raise HTTPException(422,'Aufnahme konnte lokal nicht erkannt werden')
        return {'text':text,'local':True}

    @app.post('/api/webhooks/messenger')
    async def messenger(request: Request, x_webhook_secret: str = Header(default='')):
        if not s.messenger_secret or not secrets.compare_digest(x_webhook_secret,s.messenger_secret):
            raise HTTPException(403,'Webhook deaktiviert oder ungültig')
        # Provider verification, signature checks, normalization and outbound adapter are extension points.
        raise HTTPException(501,'Messenger-Adapter noch nicht implementiert')
    frontend = Path(__file__).resolve().parent.parent/'frontend'
    app.mount('/static',StaticFiles(directory=frontend),name='static')
    @app.get('/sw.js')
    async def service_worker():
        return FileResponse(frontend/'sw.js',media_type='application/javascript',headers={'Cache-Control':'no-cache','Service-Worker-Allowed':'/'})

    @app.get('/')
    async def index():
        return FileResponse(frontend/'index.html')
    return app

app = create_app()
