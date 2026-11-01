"""Authenticated local connection setup. Secrets are write-only over the API."""
import asyncio
import imaplib
import os
from pathlib import Path
from typing import Literal
import httpx
from dotenv import set_key
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ConfigDict, field_validator
from .intelligence import generate
from .icloud import ICloudConfig
from .public_data import PublicConfig,PublicData,DeviceLocation
from .smarthome import IoBrokerConfig, read_iobroker, scope

SERVICE_FIELDS = {
    'icloud_calendar':{'icloud_calendar_enabled','icloud_calendar_user','icloud_calendar_password','icloud_calendar_names'},
    'icloud_contacts':{'icloud_contacts_enabled','icloud_contacts_user','icloud_contacts_password'},
    'weather':{'weather_enabled'},
    'holidays':{'holidays_enabled'},
    'llm': {'llm_provider','llm_model','llm_api_key','allow_external_llm','chat_llm_enabled','gemini_paid_confirmed'},
    'imap': {'imap_host','imap_port','imap_user','imap_password','imap_folder','imap_archive_enabled','imap_archive_all_folders'},
    'paperless': {'paperless_url','paperless_token'},
    'ntfy': {'ntfy_url','ntfy_token','ntfy_mode','ntfy_interval_seconds','ntfy_history_urls','ntfy_history_enabled'},
    'iobroker': set(IoBrokerConfig.model_fields),
}

class ModelRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    provider: Literal['openai','gemini']
    api_key: str | None = Field(default=None, max_length=4096)

SECRET_FIELDS = {'llm_api_key','imap_password','paperless_token','ntfy_token','iobroker_password','iobroker_token','icloud_calendar_password','icloud_contacts_password'}
class ConnectionConfig(IoBrokerConfig,PublicConfig,ICloudConfig):
    model_config = ConfigDict(extra='forbid')
    llm_provider: Literal['openai','gemini'] = 'openai'
    llm_model: str = Field(default='',max_length=150,pattern=r'^[a-zA-Z0-9._-]*$')
    llm_api_key: str | None = None
    allow_external_llm: bool = False
    chat_llm_enabled: bool | None = None
    gemini_paid_confirmed: bool = False
    imap_host: str = ''
    imap_port: int = Field(default=993,ge=1,le=65535)
    imap_user: str = ''
    imap_password: str | None = None
    imap_folder: str = 'INBOX'
    imap_archive_enabled: bool = True
    imap_archive_all_folders: bool = True
    paperless_url: str = ''
    paperless_token: str | None = None
    ntfy_url: str = ''
    ntfy_token: str | None = None
    ntfy_history_urls: str = Field(default='',max_length=6000)
    ntfy_history_enabled: bool = True
    ntfy_mode: Literal['off','important','digest'] = 'important'
    ntfy_interval_seconds: int = Field(default=3600,ge=60,le=86400)

    @field_validator('paperless_url','ntfy_url')
    @classmethod
    def safe_service_url(cls,value):
        from urllib.parse import urlsplit
        if not value:return value
        try:
            parts=urlsplit(value)
            valid=parts.scheme in ('http','https') and parts.hostname and not (parts.username or parts.password or parts.query or parts.fragment)
            port=parts.port
        except ValueError:valid=False
        if not valid or len(value)>2048 or any(character.isspace() or ord(character)<32 or ord(character)==127 for character in value):
            raise ValueError('Vollständige HTTP(S)-Adresse ohne eingebettete Zugangsdaten, URL-Parameter oder Fragment verwenden')
        return value.rstrip('/')

def connection_router(s, worker, auth, env_path=Path('.env')):
    router = APIRouter(prefix='/api/connections',dependencies=[Depends(auth)])
    @router.get('')
    async def get_config():
        return {**{k:getattr(s,k) for k in ConnectionConfig.model_fields if k not in SECRET_FIELDS},'secrets':{k:bool(getattr(s,k)) for k in SECRET_FIELDS},'chat_llm_enabled':s.chat_llm_enabled if s.chat_llm_enabled is not None else s.allow_external_llm}
    async def persist(value, service=None):
        from .config import Settings
        changes = {k:v for k,v in value.model_dump(exclude_unset=True).items() if v is not None}
        if service and not changes.keys() <= SERVICE_FIELDS[service]:
            raise HTTPException(422,'Diese Felder gehören zu einer anderen Verbindung')
        async with worker.settings_lock:
            if 'llm_provider' in changes and changes['llm_provider'] != s.llm_provider:
                # Never accidentally reuse the other provider's stored credential.
                if not changes.get('llm_api_key'):
                    raise HTTPException(422,'Beim Anbieterwechsel einen neuen API-Key eingeben')
                changes.setdefault('llm_model','')
                changes.setdefault('allow_external_llm',False)
                changes.setdefault('chat_llm_enabled',False)
                changes.setdefault('gemini_paid_confirmed',False)
            validated = Settings(**{**s.model_dump(),**changes})
            for field in ('paperless_url','ntfy_url'):
                url = getattr(validated,field)
                if url and not url.startswith(('http://','https://')):
                    raise HTTPException(422,'URLs müssen mit http:// oder https:// beginnen')
            from .notifications import history_urls,validate_history_url
            if len(history_urls(validated))>10:raise HTTPException(422,'Maximal zehn ntfy-Topics')
            try:
                for url in history_urls(validated):validate_history_url(url,validated)
            except ValueError as exc:raise HTTPException(422,str(exc))
            if not service or service == 'llm':
                enabled=validated.allow_external_llm or validated.chat_llm_enabled
                if enabled and not (validated.llm_model and validated.llm_api_key):
                    raise HTTPException(422,'Für externe KI werden Modell und API-Key benötigt')
                if enabled and validated.llm_provider=='gemini' and not validated.gemini_paid_confirmed:
                    raise HTTPException(422,'Für sensible Daten zuerst geeignete Gemini-Vertrags-/Abrechnungseinstellungen bestätigen')
            temp = env_path.with_suffix('.env.tmp')
            temp.touch(mode=0o600, exist_ok=False)
            try:
                temp.write_text(env_path.read_text() if env_path.exists() else '')
                for k,v in changes.items():
                    set_key(str(temp), k.upper(), str(v).lower() if isinstance(v,bool) else str(v))
                os.chmod(temp,0o600)
                os.replace(temp,env_path)
            finally:
                temp.unlink(missing_ok=True)
            for k,v in changes.items():
                setattr(s,k,v)
            if any(key.startswith('icloud_') for key in changes):
                worker.personal.retry.clear()
                worker.personal.status.clear()
        return {'saved':True}

    @router.put('')
    async def save_config(value: ConnectionConfig):
        return await persist(value)

    @router.put('/{service}')
    async def save_service(service: Literal['llm','imap','paperless','ntfy','iobroker','weather','holidays','icloud_calendar','icloud_contacts'], value: ConnectionConfig):
        return await persist(value, service)

    @router.post('/llm/models')
    async def models(value: ModelRequest):
        key = value.api_key or (s.llm_api_key if value.provider == s.llm_provider else '')
        if not key:
            raise HTTPException(422,'Bitte zuerst den API-Key für diesen Anbieter eingeben')
        try:
            async with httpx.AsyncClient(timeout=20) as client:
                if value.provider == 'openai':
                    response = await client.get('https://api.openai.com/v1/models',headers={'Authorization':'Bearer '+key})
                    response.raise_for_status()
                    found = [{'id':m['id'],'name':m['id']} for m in response.json()['data']]
                else:
                    found, page, seen = [], '', set()
                    while True:
                        response = await client.get('https://generativelanguage.googleapis.com/v1beta/models',headers={'x-goog-api-key':key},params={'pageSize':1000,**({'pageToken':page} if page else {})})
                        response.raise_for_status()
                        data = response.json()
                        for model in data.get('models',[]):
                            if 'generateContent' in model.get('supportedGenerationMethods',[]):
                                model_id = model['name'].removeprefix('models/')
                                found.append({'id':model_id,'name':model.get('displayName',model_id)})
                        page = data.get('nextPageToken')
                        if not page:
                            break
                        if page in seen:
                            raise ValueError('Pagination loop')
                        seen.add(page)
            return {'models':sorted(found,key=lambda m:m['id'])}
        except Exception as exc:
            code = exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
            raise HTTPException(502,'Modelle konnten nicht geladen werden'+(f' (HTTP {code})' if code else '')+'. API-Key und Anbieter prüfen.')

    @router.get('/iobroker/states')
    async def smart_states():
        snapshot=worker.store.smart_snapshot(scope(s)) if s.iobroker_url else {'checked':None,'states':[]}
        return {**snapshot,'enabled':s.iobroker_enabled,'configured':bool(s.iobroker_url),'poll_seconds':s.poll_seconds,'source':worker.status['sources'].get('iobroker',{})}

    @router.post('/iobroker/refresh')
    async def refresh_smart_states():
        if not s.iobroker_url:
            raise HTTPException(422,'Bitte zuerst ioBroker speichern')
        async with worker.lock:
            await worker.poll_iobroker(force=True)
        result=worker.status['sources'].get('iobroker',{})
        if result.get('error'):
            raise HTTPException(502,result['error'])
        return await smart_states()

    public_provider=PublicData(s,worker.store)
    @router.post('/{service}/preview')
    async def preview(service:Literal['weather','holidays'],location:DeviceLocation):
        from datetime import datetime
        from zoneinfo import ZoneInfo
        if not getattr(s,service+'_enabled'):raise HTTPException(422,'Diese Verbindung zuerst aktivieren und speichern')
        try:
            day=datetime.now(ZoneInfo('Europe/Berlin')).date()
            await (public_provider.weather(location,day) if service=='weather' else public_provider.holidays(location,day))
            return {'ok':True,'message':'Verbindung erfolgreich · Daten für den Gerätestandort abrufbar.'}
        except Exception:raise HTTPException(502,'Daten für diesen Standort nicht abrufbar.')

    @router.post('/{service}/test')
    async def test(service: Literal['llm','imap','paperless','ntfy','iobroker','internet','icloud_calendar','icloud_contacts']):
        try:
            if service in ('icloud_calendar','icloud_contacts'):
                from datetime import datetime,timedelta
                from zoneinfo import ZoneInfo
                kind=service.removeprefix('icloud_')
                if not getattr(s,service+'_enabled'):raise ValueError('Verbindung zuerst aktivieren')
                start=datetime.now(ZoneInfo('Europe/Berlin')).replace(hour=0,minute=0,second=0,microsecond=0)
                result=await worker.personal.refresh(kind,start,start+timedelta(days=1),force=True) if kind=='calendar' else await worker.personal.refresh(kind,force=True)
                if not result or result['stale']:raise HTTPException(502,worker.personal.status.get(service,{}).get('error','iCloud-Abruf fehlgeschlagen; bitte erneut versuchen.'))
                return {'ok':True,'message':'iCloud lesend verbunden · '+str(len(result['records']))+(' Termine im Testzeitraum' if kind=='calendar' else ' Kontakte')+' · Sammlungen: '+', '.join(result['collections'])}
            if service=='llm':
                # Explicit test sends no personal data and does not enable monitoring.
                test_settings = s.model_copy(update={'allow_external_llm':True,'gemini_paid_confirmed':True})
                await generate(test_settings,'Antworte nur mit OK.',{'test':'Verbindungstest ohne persönliche Daten'})
            elif service=='iobroker':
                states=await read_iobroker(s)
                return {'ok':True,'message':f'ioBroker erreichbar · {len(states)} Datenpunkte passen zu deinen Mustern.'}
            elif service=='imap':
                if not s.imap_host:
                    raise ValueError('Missing host')
                def check():
                    with imaplib.IMAP4_SSL(s.imap_host,s.imap_port,timeout=15) as box:
                        box.login(s.imap_user,s.imap_password)
                        status,_ = box.select(s.imap_folder,readonly=True)
                        if status!='OK':
                            raise ValueError('Invalid folder')
                await asyncio.to_thread(check)
            elif service=='ntfy':
                if not s.ntfy_url:
                    raise ValueError('Missing URL')
                async with httpx.AsyncClient(timeout=15) as client:
                    response=await client.post(s.ntfy_url,content=b'Butler: Verbindungstest erfolgreich.',headers={'Authorization':'Bearer '+s.ntfy_token} if s.ntfy_token else {})
                    response.raise_for_status()
            else:
                if service=='paperless' and not s.paperless_url:
                    raise ValueError('Missing URL')
                url = s.paperless_url.rstrip('/')+'/api/documents/?page_size=1' if service=='paperless' else 'https://example.com'
                async with httpx.AsyncClient(timeout=15) as client:
                    response=await client.get(url,headers={'Authorization':'Token '+s.paperless_token} if service=='paperless' else {})
                    response.raise_for_status()
                    if service=='paperless' and 'results' not in response.json():
                        raise ValueError('Invalid Paperless response')
            return {'ok':True,'message':'Internetzugriff erreichbar (noch keine Websuche).' if service=='internet' else 'Verbindung erfolgreich geprüft.'}
        except HTTPException:
            raise
        except Exception as exc:
            # Never include provider exception text: it can contain credentials or URLs.
            code = exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
            raise HTTPException(502, 'Verbindung fehlgeschlagen'+(f' (HTTP {code})' if code else '')+'. Zugangsdaten, Freigabe und Adresse prüfen.')
    return router
