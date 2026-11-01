"""Read-only ioBroker snapshots. No state writes or LLM calls."""
import hashlib
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit
import httpx
from pydantic import BaseModel, Field, field_validator
from typing import Literal

SENSITIVE_ID = r'password|passwd|token|secret|api.?key|aes.?key|private.?key|auth.?key'

DEFAULT_PATTERNS = '*.connected,*.reachable,*.online,*.UNREACH'

class IoBrokerConfig(BaseModel):
    iobroker_url: str = ''
    iobroker_api: Literal['simple','rest'] = 'simple'
    iobroker_user: str = ''
    iobroker_password: str | None = None
    iobroker_token: str | None = None
    iobroker_patterns: str = Field(default=DEFAULT_PATTERNS,max_length=2000)
    iobroker_enabled: bool = False
    iobroker_control_enabled: bool = False
    iobroker_controls: str = Field(default='[]',max_length=16000)

    @field_validator('iobroker_controls')
    @classmethod
    def valid_controls(cls,value):
        from .controls import devices
        return json.dumps([device.model_dump() for device in devices(value)],ensure_ascii=False)

    @field_validator('iobroker_url')
    @classmethod
    def valid_url(cls, value):
        if value:
            parts=urlsplit(value)
            if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                raise ValueError('ioBroker: Basis-URL ohne Zugangsdaten, Query oder Fragment verwenden')
        return value.rstrip('/')

    @field_validator('iobroker_patterns')
    @classmethod
    def valid_patterns(cls,value):
        parts=patterns(value)
        if not parts or len(parts)>10:
            raise ValueError('Ein bis zehn ioBroker-Muster angeben')
        return ','.join(parts)


def patterns(value):
    return list(dict.fromkeys(p.strip() for p in re.split(r'[,\n]',value) if p.strip()))


def scope(settings):
    raw=json.dumps([settings.iobroker_url.rstrip('/'),settings.iobroker_api,settings.iobroker_user,patterns(settings.iobroker_patterns)])
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def classify(state_id,state):
    value=state.get('val')
    if state.get('q',0) not in (0,None):
        return 'unknown'
    if isinstance(value,bool):
        online=value
    elif value in (0,1,'0','1','true','false','True','False'):
        online=value in (1,'1','true','True')
    else:
        return 'unknown'
    suffix=state_id.rsplit('.',1)[-1].lower()
    if suffix in ('unreach','unreachable'):
        online=not online
    elif suffix not in ('connected','reachable','online','alive'):
        return 'value'  # An off light is not an offline light.
    return 'online' if online else 'offline'



def iobroker_error(exc, settings):
    """Safe actionable diagnostics; never expose response bodies or credentials."""
    hint=' Simple-API-/REST-API-Adresse verwenden; Port 8081 ist normalerweise die Admin-Oberfläche.'
    if not settings.iobroker_url:return 'ioBroker-Adresse fehlt.'
    if isinstance(exc,httpx.TimeoutException):return 'ioBroker antwortet nicht rechtzeitig. Erreichbarkeit vom Butler/LXC und Firewall prüfen.'
    if isinstance(exc,httpx.ConnectError):return 'Keine Verbindung zu ioBroker möglich. Adapter gestartet, Adresse, Port und Firewall vom Butler/LXC aus prüfen.'
    if isinstance(exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        if code in (401,403):return f'ioBroker verweigert den Zugriff (HTTP {code}). Anmeldung und Leserechte des API-Adapters prüfen.'
        if code==404:return 'ioBroker-API-Endpunkt nicht gefunden (HTTP 404). Gewählten Adaptertyp und Basis-Adresse prüfen.'+hint
        if 300<=code<400:return 'ioBroker leitet auf eine andere Seite weiter. Direkte API-Adresse statt Anmeldeseite verwenden.'+hint
        return f'ioBroker-API meldet HTTP {code}. Adapterstatus und dessen Protokoll prüfen.'
    if isinstance(exc,(ValueError,TypeError)):return 'ioBroker liefert keine gültige Zustandsliste. Adaptertyp und API-Adresse prüfen.'+hint
    return 'ioBroker-Abfrage fehlgeschlagen. Adapterstatus und Erreichbarkeit vom Butler/LXC prüfen.'

async def read_iobroker(settings):
    if not settings.iobroker_url:
        raise ValueError('ioBroker-Adresse fehlt')
    config=IoBrokerConfig(**{k:getattr(settings,k) for k in IoBrokerConfig.model_fields})
    headers={'Authorization':'Bearer '+config.iobroker_token} if config.iobroker_token else {}
    auth=httpx.BasicAuth(config.iobroker_user,config.iobroker_password or '') if config.iobroker_user and not config.iobroker_token else None
    endpoint='/states' if config.iobroker_api=='simple' else '/v1/states'
    param='pattern' if config.iobroker_api=='simple' else 'filter'
    result={}
    async with httpx.AsyncClient(timeout=15,headers=headers,auth=auth,follow_redirects=False) as client:
        for pattern in patterns(config.iobroker_patterns):
            response=await client.get(config.iobroker_url+endpoint,params={param:pattern})
            response.raise_for_status()
            data=response.json()
            if data==[]:  # rest-api uses [] for an empty result.
                data={}
            if not isinstance(data,dict) or 'error' in data:
                raise ValueError('Unerwartete ioBroker-Antwort')
            for state_id,state in data.items():
                if state is None:
                    state={'val':None}
                if not isinstance(state,dict) or 'val' not in state:
                    raise ValueError('Ungültige Zustandsliste')
                # Credentials never belong in the inventory, even with a broad user pattern.
                if re.search(SENSITIVE_ID,state_id,re.I):
                    continue
                common=state.get('common') or {}
                name=common.get('name',state_id)
                if isinstance(name,dict):
                    name=name.get('de') or name.get('en') or state_id
                result[state_id]={'id':state_id,'name':str(name)[:300],'value':state['val'],'status':classify(state_id,state),'source_ts':state.get('ts')}
            if len(result)>10000:
                raise ValueError('Zu viele Datenpunkte; Muster eingrenzen')
    return list(result.values())


def utc_now():
    return datetime.now(timezone.utc).isoformat()


async def state_inventory(client,settings):
    async with client.stream('GET',settings.iobroker_url.rstrip('/')+'/objects',params={'type':'state'}) as response:
        response.raise_for_status();buffer=bytearray()
        async for chunk in response.aiter_bytes():
            buffer.extend(chunk)
            if len(buffer)>32*1024*1024:raise ValueError('ioBroker inventory too large')
    objects=json.loads(buffer);inventory=[]
    if not isinstance(objects,dict) or 'error' in objects:raise ValueError('Invalid ioBroker inventory')
    for state_id,obj in objects.items():
        if not isinstance(obj,dict) or obj.get('type')!='state':continue
        common=obj.get('common') or {}
        if common.get('read') is False or re.search(SENSITIVE_ID,state_id,re.I):continue
        name=common.get('name') or state_id
        if isinstance(name,dict):name=name.get('de') or name.get('en') or state_id
        # Native adapter configuration never enters the model context or the local inventory.
        alias=(common.get('alias') or {}).get('id') if isinstance(common.get('alias'),dict) else None
        if isinstance(alias,dict):alias=alias.get('read')
        if isinstance(alias,str) and re.search(SENSITIVE_ID,alias,re.I):continue
        inventory.append({'id':state_id,'name':str(name)[:300],'role':common.get('role',''),'unit':common.get('unit',''),'value_type':common.get('type'),'alias_id':alias if isinstance(alias,str) else None})
    return inventory


async def relevant_states(settings,queries,inventory=None):
    """Discover readable signals by ioBroker object names/IDs; retrieve selected values only."""
    if not settings.iobroker_url or settings.iobroker_api!='simple':return [],inventory
    import re
    from urllib.parse import quote
    terms=list(dict.fromkeys(word.casefold() for query in queries for word in re.findall(r'\w{3,}',query)))[:20]
    if not terms:return [],inventory
    headers={'Authorization':'Bearer '+settings.iobroker_token} if settings.iobroker_token else {}
    auth=httpx.BasicAuth(settings.iobroker_user,settings.iobroker_password or '') if settings.iobroker_user and not settings.iobroker_token else None
    async with httpx.AsyncClient(timeout=15,headers=headers,auth=auth,follow_redirects=False) as client:
        if inventory is None:
            inventory=await state_inventory(client,settings)
        scored=[]
        for obj in inventory:
            haystack=(obj['id']+' '+obj['name']).casefold()
            score=sum(term in haystack for term in terms)
            if score:scored.append((score,obj))
        chosen=[obj for _,obj in sorted(scored,key=lambda pair:pair[0],reverse=True)[:20]]
        async def get(obj):
            r=await client.get(settings.iobroker_url.rstrip('/')+'/get/'+quote(obj['id'],safe=''))
            r.raise_for_status();state=r.json()
            if not isinstance(state,dict) or 'val' not in state:return None
            return {**obj,'value':state['val'],'status':classify(obj['id'],state),'source_ts':state.get('ts')}
        responses=await __import__('asyncio').gather(*(get(obj) for obj in chosen),return_exceptions=True)
        return [r for r in responses if isinstance(r,dict)],inventory
