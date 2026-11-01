"""Explicitly approved actuators. No LLM writes, no toggle/retry, confirmation per action."""
import asyncio
import hashlib
import json
import math
import re
import secrets
import time
from urllib.parse import quote
import httpx
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,ConfigDict,Field,model_validator,field_validator

STATE=r'^(?!system\.)(?!script\.)(?!javascript\.)(?!.*(?:password|token|secret))[a-zA-Z0-9_][a-zA-Z0-9_.:-]{0,199}$'
class Device(BaseModel):
    model_config=ConfigDict(extra='forbid',regex_engine='python-re')
    name:str=Field(min_length=2,max_length=80)
    kind:str=Field(pattern=r'^(blind|switch)$')
    state_id:str=Field(pattern=STATE)
    stop_id:str|None=Field(default=None,pattern=STATE)
    open_value:float=Field(default=100,allow_inf_nan=False)
    closed_value:float=Field(default=0,allow_inf_nan=False)
    @field_validator('name')
    @classmethod
    def clean_name(cls,value):
        value=value.strip()
        if len(value)<2 or not re.search(r'\w',value):raise ValueError('Eindeutigen Gerätenamen angeben')
        return value
    @model_validator(mode='after')
    def distinct(self):
        if self.kind=='blind' and self.open_value==self.closed_value:raise ValueError('Offen- und Geschlossen-Wert müssen verschieden sein')
        if self.stop_id==self.state_id:raise ValueError('Stopp benötigt einen separaten Datenpunkt')
        return self

def devices(raw):
    values=json.loads(raw or '[]')
    if not isinstance(values,list) or len(values)>50:raise ValueError('Höchstens 50 Geräte freigeben')
    result=[Device.model_validate(value) for value in values]
    if len({d.name.strip().casefold() for d in result})!=len(result):raise ValueError('Gerätenamen müssen eindeutig sein')
    return result

def control_intent(text):
    """Only direct imperative user messages; questions and negation never actuate."""
    text=text.strip().casefold().rstrip('.!')
    text=re.sub(r'^bitte\s+','',text)
    if re.search(r'\b(nicht|kein\w*|und|oder|vielleicht|falls|wenn|alle)\b|[?;\n]',text):return None
    if not re.match(r'^(?:fahr(?:e)?|schalte|schalt|öffne|oeffne|schließe|schliesse|stoppe|stopp)\b',text):return None
    if re.search(r'\bstopp(?:e)?\b|\banhalten\b',text):return {'action':'stop'}
    percent=re.search(r'\b(?:auf)\s+(\d{1,3})\s*(?:%|prozent)\s*$',text)
    if percent:return {'action':'position','percent':int(percent[1])}
    if re.search(r'\b(?:hoch|rauf|öffne|oeffne|auf)\b',text):return {'action':'position','percent':100}
    if re.search(r'\b(?:runter|herunter|zu|schließe|schliesse)\b',text):return {'action':'position','percent':0}
    if re.search(r'\b(?:ein|an)\s*$',text):return {'action':'switch','value':True}
    if re.search(r'\baus\s*$',text):return {'action':'switch','value':False}
    return None

def config_identity(settings):
    return hashlib.sha256(json.dumps([settings.iobroker_url,settings.iobroker_api,settings.iobroker_user,settings.iobroker_password,settings.iobroker_token,settings.iobroker_controls,settings.iobroker_control_enabled]).encode()).hexdigest()

class ControlService:
    def __init__(self,settings,store):self.settings=settings;self.store=store;self.pending={};self.lock=asyncio.Lock()
    def plan(self,text,owner):
        intent=control_intent(text)
        if not intent:return None
        base={'sources':[],'diagnostics':[],'external_used':False,'answer_mode':'local','query_kind':'device_control'}
        if not self.settings.iobroker_control_enabled:return {**base,'answer':'Die Gerätesteuerung ist noch nicht freigegeben. Unter Einstellungen → ioBroker kannst du Lampen, Steckdosen und Rollos gezielt freigeben.'}
        names=devices(self.settings.iobroker_controls)
        compatible=[d for d in names if (d.kind=='switch')==(intent['action']=='switch')]
        def normalized(value):return ' '.join(word for word in re.findall(r'\w+',value.casefold()) if word not in {'das','die','der','den','dem','im','in','am','bitte'})
        matches=[d for d in compatible if re.search(r'(?<!\w)'+re.escape(normalized(d.name))+r'(?!\w)',normalized(text))]
        if len(matches)!=1:return {**base,'answer':'Welches freigegebene Gerät meinst du genau? Bitte verwende den vollständigen Namen aus den ioBroker-Einstellungen.'}
        device=matches[0]
        if intent.get('percent',0)>100:return {**base,'answer':'Die Rollo-Position muss zwischen 0 und 100 Prozent liegen. 100 Prozent bedeutet vollständig offen.'}
        if intent['action']=='stop' and not device.stop_id:return {**base,'answer':'Für dieses Rollo ist noch kein Stopp-Datenpunkt freigegeben.'}
        label=device.name+(' einschalten' if intent['action']=='switch' and intent['value'] else ' ausschalten' if intent['action']=='switch' else ' stoppen' if intent['action']=='stop' else f" auf {intent['percent']} % Öffnung fahren")
        token=secrets.token_hex(16)
        self.pending={key:value for key,value in self.pending.items() if value['expires']>time.monotonic()}
        if len(self.pending)>=256:self.pending.pop(next(iter(self.pending)))
        self.pending[token]={'owner':owner,'expires':time.monotonic()+300,'device':device,'intent':intent,'identity':config_identity(self.settings),'label':label}
        return {**base,'answer':'Soll ich '+label+'?','control_action':{'token':token,'label':label}}
    async def execute(self,token,owner):
        async with self.lock:
            plan=self.pending.get(token)
            if not plan or plan['owner']!=owner:raise HTTPException(404,'Schaltauftrag nicht gefunden')
            if plan['expires']<time.monotonic() or plan['identity']!=config_identity(self.settings):
                self.pending.pop(token,None);raise HTTPException(409,'Freigabe abgelaufen oder Einstellungen geändert. Bitte den Befehl erneut stellen.')
            self.pending.pop(token) # Consume before I/O. Never replay an uncertain write.
            if not self.settings.iobroker_control_enabled or self.settings.iobroker_api!='simple':raise HTTPException(409,'Steuerung benötigt eine aktivierte Simple-API-Verbindung.')
            device=plan['device'];intent=plan['intent'];state_id=device.stop_id if intent['action']=='stop' else device.state_id
            value=True if intent['action']=='stop' else intent.get('value') if intent['action']=='switch' else device.closed_value+(device.open_value-device.closed_value)*intent['percent']/100
            headers={'Authorization':'Bearer '+self.settings.iobroker_token} if self.settings.iobroker_token else {}
            auth=httpx.BasicAuth(self.settings.iobroker_user,self.settings.iobroker_password or '') if self.settings.iobroker_user and not self.settings.iobroker_token else None
            base=self.settings.iobroker_url.rstrip('/')
            try:
                async with httpx.AsyncClient(timeout=8,headers=headers,auth=auth,follow_redirects=False) as client:
                    before=await client.get(base+'/get/'+quote(state_id,safe=''));before.raise_for_status();state=before.json();common=state.get('common') or {}
                    role=common.get('role','');kind=common.get('type')
                    allowed=(intent['action']=='switch' and kind=='boolean' and role in ('switch','switch.light','switch.socket','switch.power')) or (intent['action']=='position' and kind=='number' and role in ('level.blind','level.shutter')) or (intent['action']=='stop' and kind=='boolean' and role.startswith('button.stop'))
                    if common.get('write') is not True or not allowed:raise HTTPException(409,'Der freigegebene Datenpunkt ist kein passender schreibbarer Aktor. Rolle und Typ in ioBroker prüfen.')
                    if intent['action']=='position':
                        low,high=common.get('min'),common.get('max')
                        if not all(isinstance(v,(float,int)) and not isinstance(v,bool) and math.isfinite(v) for v in (low,high)) or not low<=min(device.open_value,device.closed_value)<=max(device.open_value,device.closed_value)<=high:raise HTTPException(409,'Offen-/Geschlossen-Werte passen nicht zum ioBroker-Wertebereich.')
                    await asyncio.to_thread(self.store.save_sync_state,'control:'+token,{'owner':owner,'device':device.name,'action':intent,'status':'attempting','time':time.time()})
                    if plan['identity']!=config_identity(self.settings):raise HTTPException(409,'Gerätekonfiguration während der Prüfung geändert. Bitte Befehl erneut stellen.')
                    result=await client.post(base+'/setBulk',content=state_id+'='+(json.dumps(value) if isinstance(value,bool) else format(value,'.12g')),headers={'Content-Type':'text/plain'})
                    result.raise_for_status();reply=result.json()
                    if not isinstance(reply,list) or len(reply)!=1 or not isinstance(reply[0],dict) or reply[0].get('id')!=state_id or reply[0].get('error'):raise RuntimeError('Unconfirmed write')
                    message='Befehl übermittelt: '+plan['label']+'.'
                    confirmed=False
                    try:
                        after=await client.get(base+'/get/'+quote(state_id,safe=''));after.raise_for_status();reported=after.json()
                        actual=reported.get('val')
                        confirmed=intent['action']!='stop' and reported.get('ack') is True and reported.get('q',0) in (0,None) and ((isinstance(actual,bool) and actual==value) if intent['action']=='switch' else isinstance(actual,(float,int)) and not isinstance(actual,bool) and abs(actual-value)<=abs(device.open_value-device.closed_value)*.01)
                    except Exception:pass
                    message+=' Der Zielzustand wurde vom Adapter bestätigt.' if confirmed else ' Der tatsächliche Zielzustand ist noch nicht bestätigt.'
                    return {'answer':message,'confirmed':confirmed}
            except HTTPException:raise
            except Exception:raise HTTPException(502,'Der Befehl konnte nicht sicher bestätigt werden. Möglicherweise wurde er bereits ausgeführt. Bitte den Gerätezustand prüfen; es wird nicht automatisch erneut geschaltet.')

def control_router(service,auth):
    router=APIRouter(prefix='/api/controls',dependencies=[Depends(auth)])
    class Confirm(BaseModel):token:str=Field(pattern=r'^[a-f0-9]{32}$')
    @router.post('/cancel')
    async def cancel(value:Confirm,request:Request):
        plan=service.pending.get(value.token)
        if not plan or plan['owner']!=(request.state.user or {}).get('id'):raise HTTPException(404,'Schaltauftrag nicht gefunden')
        service.pending.pop(value.token,None)
        return {'cancelled':True}
    @router.post('/execute')
    async def execute(value:Confirm,request:Request):
        owner=(request.state.user or {}).get('id')
        return await service.execute(value.token,owner)
    return router
