import asyncio
import ipaddress
from datetime import datetime
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager, suppress
import secrets
import hashlib
import hmac
import time
from typing import Annotated
from fastapi import FastAPI, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pathlib import Path
from pydantic import BaseModel, Field
from .config import Settings
from .store import Store
from .worker import Worker
from .intelligence import generate, ProviderLimited, provider_is_limited
from .planning import plan_question
from .smarthome import scope
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
    s = settings or Settings()
    store = Store(s.data_dir)
    worker = Worker(s,store)
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
    app = FastAPI(title='Lokaler KI-Butler', lifespan=lifespan)
    from urllib.parse import urlsplit
    public=urlsplit(s.public_origin)
    if s.public_origin and (public.scheme!='https' or not public.hostname or public.username or public.password or public.path not in ('','/') or public.query or public.fragment):
        raise RuntimeError('PUBLIC_ORIGIN muss eine reine HTTPS-Origin sein')
    allowed_hosts=['localhost','127.0.0.1','testserver']+([public.hostname] if public.hostname else [])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts)
    app.state.store, app.state.worker = store, worker
    @app.middleware('http')
    async def private_responses(request,call_next):
        if public.hostname and request.url.hostname==public.hostname and request.url.scheme!='https':
            return JSONResponse({'detail':'Handy-Zugriff erfordert HTTPS'},status_code=403)
        response=await call_next(request)
        if request.url.path.startswith('/api') or request.url.path=='/':
            response.headers['Cache-Control']='no-store'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Permissions-Policy']='microphone=(self)'
        return response
    pairing={'hash':'','expires':0,'attempts':0}
    session_lifetime = 30 * 24 * 60 * 60
    cookie_name = 'butler_session'
    def session_signature(expiry):
        return hmac.new(s.api_token.encode(), ('butler-session-v1:'+expiry).encode(),hashlib.sha256).hexdigest()

    async def auth(request: Request, authorization: str = Header(default='')):
        if s.api_token and authorization and secrets.compare_digest(authorization, 'Bearer '+s.api_token):
            return
        cookie = request.cookies.get(cookie_name,'')
        try:
            expiry, signature = cookie.split('.',1)
            valid = bool(s.api_token) and time.time() < int(expiry) <= time.time()+session_lifetime+60 and hmac.compare_digest(signature,session_signature(expiry))
        except (ValueError,TypeError):
            valid = False
        if not valid:
            raise HTTPException(401,'Bitte einmal mit dem lokalen API-Token anmelden')
        if request.method not in ('GET','HEAD','OPTIONS') and request.headers.get('origin') != str(request.base_url).rstrip('/'):
            raise HTTPException(403,'Anfrage stammt nicht von dieser Butler-Oberfläche')

    @app.post('/api/session')
    async def login(request: Request, authorization: str = Header(default='')):
        if not s.api_token or not secrets.compare_digest(authorization,'Bearer '+s.api_token):
            raise HTTPException(401,'Ungültiger API-Token')
        response = JSONResponse({'authenticated':True})
        expiry = str(int(time.time())+session_lifetime)
        response.set_cookie(cookie_name, expiry+'.'+session_signature(expiry),max_age=session_lifetime,httponly=True,samesite='strict',secure=request.url.scheme=='https',path='/api')
        response.headers['Cache-Control']='no-store'
        return response

    @app.post('/api/session/local')
    async def local_login(request: Request):
        # A local desktop app: only its own origin on a loopback connection may bootstrap.
        # No proxy headers are trusted here; bind Uvicorn to 127.0.0.1.
        try:
            local=ipaddress.ip_address(request.client.host).is_loopback
        except (ValueError,AttributeError):
            local=False
        expected=str(request.base_url).rstrip('/')
        if (not local or request.url.hostname not in ('localhost','127.0.0.1')
            or request.headers.get('origin') != expected
            or request.headers.get('sec-fetch-site','same-origin') != 'same-origin'):
            raise HTTPException(403,'Automatische Verbindung ist nur von der lokalen Butler-Oberfläche möglich')
        return await login(request, 'Bearer '+s.api_token)

    @app.post('/api/session/pairing',dependencies=[Depends(auth)])
    async def create_pairing(request:Request):
        if request.url.hostname not in ('localhost','127.0.0.1') or not ipaddress.ip_address(request.client.host).is_loopback:
            raise HTTPException(403,'Kopplung nur am lokalen Rechner erstellen')
        if not s.public_origin:
            raise HTTPException(409,'Zuerst eine HTTPS-Adresse als PUBLIC_ORIGIN einrichten')
        code=''.join(secrets.choice('0123456789') for _ in range(10))
        pairing.update(hash=hashlib.sha256(code.encode()).hexdigest(),expires=time.time()+600,attempts=0)
        return {'code':code,'expires_in':600,'url':s.public_origin}

    class Pairing(BaseModel):
        code:str=Field(min_length=10,max_length=10,pattern=r'^\d{10}$')

    @app.post('/api/session/pair')
    async def pair(value:Pairing,request:Request):
        if not s.public_origin or str(request.base_url).rstrip('/')!=s.public_origin.rstrip('/') or request.headers.get('origin')!=s.public_origin.rstrip('/') or request.url.scheme!='https':
            raise HTTPException(403,'Kopplung nur über die konfigurierte HTTPS-Adresse')
        if pairing['expires']<time.time() or pairing['attempts']>=10:
            raise HTTPException(403,'Kopplung abgelaufen. Am Rechner neu erstellen.')
        pairing['attempts']+=1
        if not hmac.compare_digest(hashlib.sha256(value.code.encode()).hexdigest(),pairing['hash']):
            raise HTTPException(403,'Kopplungscode ungültig')
        pairing.update(hash='',expires=0)
        return await login(request,'Bearer '+s.api_token)

    @app.get('/api/session',dependencies=[Depends(auth)])
    async def session_status():
        return {'authenticated':True}

    @app.delete('/api/session',dependencies=[Depends(auth)])
    async def logout():
        response=JSONResponse({'authenticated':False})
        response.delete_cookie(cookie_name,path='/api',httponly=True,samesite='strict')
        return response

    from .connections import connection_router
    app.include_router(connection_router(s,worker,auth))
    protected = [Depends(auth)]
    @app.get('/api/status',dependencies=protected)
    async def status():
        return {**worker.status,'external_llm':s.chat_llm_enabled if s.chat_llm_enabled is not None else s.allow_external_llm,'worker_enabled':s.worker_enabled,'llm_limited':provider_is_limited(s)}
    @app.get('/api/sync',dependencies=protected)
    async def sync_status():
        return store.sync_overview()

    @app.get('/api/context',dependencies=protected)
    async def context():
        return store.context()
    @app.put('/api/context',dependencies=protected)
    async def update_context(value: Context):
        store.save_context(value.model_dump())
        return value
    @app.get('/api/events',dependencies=protected)
    async def events():
        return store.events()
    @app.post('/api/scan',dependencies=protected)
    async def scan():
        if worker.lock.locked():
            raise HTTPException(409,'Prüfung läuft bereits')
        await worker.tick()
        return worker.status
    @app.get('/api/conversations',dependencies=protected)
    async def conversations():
        return store.conversations()

    @app.get('/api/conversations/{conversation_id}',dependencies=protected)
    async def conversation(conversation_id:str):
        result=store.conversation(conversation_id)
        if result is None:raise HTTPException(404,'Gespräch nicht gefunden')
        return result

    @app.post('/api/chat',dependencies=protected)
    async def chat(value: Chat):
        previous=store.conversation(value.conversation_id) if value.conversation_id else None
        if value.conversation_id and previous is None:raise HTTPException(404,'Gespräch nicht gefunden')
        history=[{'role':message['role'],'text':message['text'][:1600]} for message in (previous['messages'][-6:] if previous else [])]
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
            local='Passende Smart-Home-Zustände sind als Kontext vorhanden. Aktiviere die externe KI für eine formulierte Auswertung deiner Frage.'
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
        result['conversation_id']=store.save_exchange(value.conversation_id,value.message,result)
        return result
    speech_lock=asyncio.Lock()
    @app.get('/api/speech',dependencies=[Depends(auth)])
    async def speech_status():
        return {'local':True,'ready':(s.data_dir/'models/whisper-base/model.bin').is_file(),'max_seconds':60}

    @app.post('/api/speech/transcribe',dependencies=[Depends(auth)])
    async def speech_transcribe(request:Request):
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
