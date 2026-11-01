"""Read-only iCloud CalDAV/CardDAV with bounded parsing and explicit source freshness."""
import asyncio
from datetime import datetime,date,time as daytime,timedelta
from zoneinfo import ZoneInfo
import hashlib
import io
import json
import re
import time
from urllib.parse import urljoin,urlsplit
from xml.sax.saxutils import escape
import httpx
from defusedxml import ElementTree as ET
from icalendar import Calendar
import recurring_ical_events
import vobject
from pydantic import BaseModel,Field
from .public_data import outdoor_question,target_day
from .retrieval import STOPWORDS

D='DAV:';C='urn:ietf:params:xml:ns:caldav';A='urn:ietf:params:xml:ns:carddav'


def icloud_error(exc,stage):
    # Only fixed messages and numeric status codes; never echo provider bodies/URLs.
    prefix='iCloud · '+stage+': '
    if isinstance(exc,httpx.HTTPStatusError):
        code=exc.response.status_code
        message={401:'Apple lehnt die Anmeldung ab. Apple-Account und app-spezifisches Passwort prüfen.',403:'Apple verweigert den Zugriff auf diese Sammlung.',404:'Die angeforderte iCloud-Sammlung wurde nicht gefunden.',400:'Apple hat die DAV-Abfrage abgelehnt.',429:'Zu viele Abrufe. Bitte kurz warten und erneut testen.'}.get(code,'Apple-Dienst derzeit nicht verfügbar.' if code>=500 else 'Apple hat den Abruf abgelehnt.')
        return prefix+message+' (HTTP '+str(code)+')'
    if isinstance(exc,(TimeoutError,httpx.TimeoutException)):return prefix+'Zeitüberschreitung beim Abruf. Bitte erneut versuchen.'
    if isinstance(exc,httpx.RequestError):return prefix+'Netzwerk- oder TLS-Verbindung zu Apple fehlgeschlagen. DNS und Internetzugang des LXC prüfen.'
    if isinstance(exc,ValueError) and str(exc) in ('iCloud-Antwort überschreitet das Größenlimit','iCloud-Sammlung ist zu groß','Zu viele iCloud-Einträge im Zeitraum','iCloud-Eintrag ist zu groß'):
        return prefix+'Die Sammlung überschreitet das unterstützte Größenlimit; bisherige Daten bleiben erhalten.'
    if stage=='Kontaktformat':return prefix+'Mindestens eine Kontaktkarte konnte nicht gelesen werden. Der bisherige Stand bleibt erhalten.'
    return prefix+'Die DAV-Antwort konnte nicht vollständig verarbeitet werden. Der bisherige Stand bleibt erhalten.'

class ICloudConfig(BaseModel):
    icloud_calendar_enabled:bool=False
    icloud_calendar_user:str=Field(default='',max_length=254)
    icloud_calendar_password:str|None=Field(default=None,max_length=1024)
    icloud_calendar_names:str=Field(default='',max_length=1000)
    icloud_contacts_enabled:bool=False
    icloud_contacts_user:str=Field(default='',max_length=254)
    icloud_contacts_password:str|None=Field(default=None,max_length=1024)


def account_scope(settings,kind):
    user=getattr(settings,'icloud_'+kind+'_user').strip().casefold()
    extra=settings.icloud_calendar_names.strip() if kind=='calendar' else ''
    return kind+':'+hashlib.sha256((user+'\n'+extra).encode()).hexdigest()[:24]


def apple_url(value,base,kind):
    url=urljoin(base,value);parts=urlsplit(url)
    server='caldav' if kind=='calendar' else 'contacts'
    if (parts.scheme!='https' or not re.fullmatch(r'(?:p\d{1,3}-)?'+server+r'\.icloud\.com',parts.hostname or '') or parts.username or parts.password or parts.query or parts.fragment or parts.port not in (None,443) or any(ord(c)<32 for c in url)):
        raise ValueError('Unzulässiges iCloud-Ziel')
    return url


class DAV:
    def __init__(self,settings,kind):
        self.stage='Verbindung';self.kind=kind;self.root='https://'+('caldav' if kind=='calendar' else 'contacts')+'.icloud.com/'
        self.user=getattr(settings,'icloud_'+kind+'_user');self.password=getattr(settings,'icloud_'+kind+'_password')
        if not self.user or not self.password:raise ValueError('Apple-Account und app-spezifisches Passwort fehlen')

    async def request(self,client,method,url,body,depth='0'):
        if method not in ('PROPFIND','REPORT'):raise ValueError('Nur lesende DAV-Methoden erlaubt')
        url=apple_url(url,self.root,self.kind)
        for _ in range(4):
            async with client.stream(method,url,content=body.encode(),headers={'Depth':depth,'Content-Type':'application/xml; charset=utf-8'}) as response:
                if response.status_code in (301,302,307,308):
                    url=apple_url(response.headers.get('location',''),url,self.kind);continue
                response.raise_for_status();data=bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data)>8*1024*1024:raise ValueError('iCloud-Antwort überschreitet das Größenlimit')
                tree=ET.fromstring(data)
                if tree.tag!='{DAV:}multistatus':raise ValueError('Unerwartete DAV-Antwort')
                return tree,url
        raise ValueError('Zu viele iCloud-Weiterleitungen')

    async def discover(self,client):
        self.stage='Anmeldung / Kontosuche'
        body=f'<d:propfind xmlns:d="DAV:"><d:prop><d:current-user-principal/></d:prop></d:propfind>'
        try:tree,base=await self.request(client,'PROPFIND',self.root,body)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code!=404:raise
            tree,base=await self.request(client,'PROPFIND',self.root+'.well-known/'+('caldav' if self.kind=='calendar' else 'carddav'),body)
        principal=tree.find('.//{DAV:}current-user-principal/{DAV:}href')
        if principal is None or not principal.text:raise ValueError('iCloud-Prinzipal fehlt')
        self.stage='Sammlungssuche'
        namespace=C if self.kind=='calendar' else A
        home='calendar-home-set' if self.kind=='calendar' else 'addressbook-home-set'
        tree,base=await self.request(client,'PROPFIND',apple_url(principal.text,base,self.kind),f'<d:propfind xmlns:d="DAV:" xmlns:x="{namespace}"><d:prop><x:{home}/></d:prop></d:propfind>')
        href=tree.find('.//{'+namespace+'}'+home+'/{DAV:}href')
        if href is None or not href.text:raise ValueError('iCloud-Sammlung fehlt')
        tree,base=await self.request(client,'PROPFIND',apple_url(href.text,base,self.kind),'<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav"><d:prop><d:displayname/><d:resourcetype/><c:supported-calendar-component-set/></d:prop></d:propfind>','1')
        result=[];collection='calendar' if self.kind=='calendar' else 'addressbook'
        for response in tree.findall('{DAV:}response'):
            for propstat in response.findall('{DAV:}propstat'):
                if ' 200 ' not in (propstat.findtext('{DAV:}status') or ''):continue
                prop=propstat.find('{DAV:}prop')
                if prop is None or prop.find('{DAV:}resourcetype/{'+namespace+'}'+collection) is None:continue
                components=prop.findall('{'+C+'}supported-calendar-component-set/{'+C+'}comp')
                if self.kind=='calendar' and components and not any(comp.get('name')=='VEVENT' for comp in components):continue
                link=response.findtext('{DAV:}href')
                if link:result.append({'url':apple_url(link,base,self.kind),'name':(prop.findtext('{DAV:}displayname') or 'Ohne Namen')[:200]})
        if not result:raise ValueError('Keine lesbaren iCloud-Sammlungen gefunden')
        if len(result)>100:raise ValueError('Zu viele iCloud-Sammlungen')
        return result

    async def read(self,settings,start=None,end=None,zone='Europe/Berlin'):
        result=[]
        async with httpx.AsyncClient(timeout=20,auth=httpx.BasicAuth(self.user,self.password),follow_redirects=False,trust_env=False) as client:
            collections=await self.discover(client)
            selected={name.strip().casefold() for name in re.split(r'[,\n]',settings.icloud_calendar_names) if name.strip()} if self.kind=='calendar' else set()
            if selected:
                available={collection['name'].strip().casefold() for collection in collections}
                if selected-available:raise ValueError('Ein ausgewählter Kalender ist nicht verfügbar')
                collections=[collection for collection in collections if collection['name'].strip().casefold() in selected]
            for collection in collections:
                if self.kind=='calendar':
                    stamp=lambda value:value.astimezone(ZoneInfo('UTC')).strftime('%Y%m%dT%H%M%SZ')
                    body=f'<c:calendar-query xmlns:d="DAV:" xmlns:c="{C}"><d:prop><d:getetag/><c:calendar-data/></d:prop><c:filter><c:comp-filter name="VCALENDAR"><c:comp-filter name="VEVENT"><c:time-range start="{stamp(start)}" end="{stamp(end)}"/></c:comp-filter></c:comp-filter></c:filter></c:calendar-query>'
                else:
                    # Ask only for fields we use: embedded photos/notes can otherwise exceed limits.
                    fields=''.join('<a:prop name="'+name+'"/>' for name in ('VERSION','UID','FN','N','EMAIL','TEL','ORG','TITLE','REV','RELATED','X-ABRELATEDNAMES','X-ABLABEL'))
                    body=f'<a:addressbook-query xmlns:d="DAV:" xmlns:a="{A}"><d:prop><d:getetag/><a:address-data>{fields}</a:address-data></d:prop><a:filter test="anyof"><a:prop-filter name="FN"/><a:prop-filter name="FN"><a:is-not-defined/></a:prop-filter></a:filter></a:addressbook-query>'
                self.stage='Kalenderabruf' if self.kind=='calendar' else 'Kontaktabruf'
                tree,base=await self.request(client,'REPORT',collection['url'],body,'1')
                field='calendar-data' if self.kind=='calendar' else 'address-data';namespace=C if self.kind=='calendar' else A
                responses=tree.findall('{DAV:}response')
                if len(responses)>5000:raise ValueError('iCloud-Sammlung ist zu groß')
                for response in responses:
                    # An incomplete/denied member must never turn into an apparently empty calendar.
                    good=[ps for ps in response.findall('{DAV:}propstat') if ' 200 ' in (ps.findtext('{DAV:}status') or '')]
                    raw=next((ps.findtext('{DAV:}prop/{'+namespace+'}'+field) for ps in good if ps.find('{DAV:}prop/{'+namespace+'}'+field) is not None),None)
                    if raw is None:raise ValueError('iCloud liefert unvollständige Einträge')
                    if len(raw)>2*1024*1024:raise ValueError('iCloud-Eintrag ist zu groß')
                    if self.kind=='calendar':result.extend(parse_events(raw,collection,start,end,zone))
                    else:
                        self.stage='Kontaktformat'
                        result.append(parse_contact(raw,account_scope(settings,'contacts')))
                        self.stage='Kontaktabruf'
                    if len(result)>5000:raise ValueError('Zu viele iCloud-Einträge im Zeitraum')
        unique={row['id']:row for row in result}
        return {'records':list(unique.values()),'collections':[row['name'] for row in collections]}


def as_datetime(value,zone):
    if isinstance(value,datetime):return value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
    return datetime.combine(value,daytime.min,zone)


def parse_events(raw,collection,start,end,zone):
    parsed=Calendar.from_ical(raw);components=parsed.walk('VEVENT')
    if len(components)>200:raise ValueError('Zu viele Kalenderkomponenten')
    for component in components:
        rule=component.get('RRULE') or {}
        if any(str(freq).upper() in ('SECONDLY','MINUTELY','HOURLY') for freq in rule.get('FREQ',[])):raise ValueError('Zu dichte Terminserie; keine vollständige Auswertung möglich')
    instances=recurring_ical_events.of(parsed).between(start,end)
    if len(instances)>2000:raise ValueError('Zu viele Wiederholungen')
    result=[];tz=ZoneInfo(zone)
    for event in instances:
        if str(event.get('STATUS','')).upper()=='CANCELLED':continue
        begins=event.decoded('DTSTART');finishes=event.decoded('DTEND',None)
        all_day=not isinstance(begins,datetime)
        if finishes is None:finishes=begins+event.decoded('DURATION',timedelta(days=1) if all_day else timedelta())
        first,last=as_datetime(begins,tz),as_datetime(finishes,tz)
        if first>=end or (last<=start and first<start):continue
        identifier=hashlib.sha256((collection['url']+'\n'+str(event.get('UID',''))+'\n'+first.isoformat()).encode()).hexdigest()
        attendees=event.get('ATTENDEE',[])
        if not isinstance(attendees,list):attendees=[attendees]
        participants=[{'email':str(person).removeprefix('mailto:')[:254],'status':str(person.params.get('PARTSTAT','NEEDS-ACTION'))[:30]} for person in attendees[:20]]
        result.append({'attendees':participants,'status':str(event.get('STATUS','CONFIRMED'))[:30],'id':'icloud_calendar:'+identifier,'title':str(event.get('SUMMARY','Termin'))[:300],'description':str(event.get('DESCRIPTION',''))[:2000],'location':str(event.get('LOCATION',''))[:500],'start':first.isoformat(),'end':last.isoformat(),'all_day':all_day,'busy':str(event.get('TRANSP','OPAQUE')).upper()!='TRANSPARENT','calendar':collection['name']})
    return result


def parse_contact(raw,scope):
    # Select fields BEFORE vobject decodes values. Even transform=False still
    # decodes binary PHOTO values and can fail on an irrelevant damaged photo.
    allowed={'VERSION','UID','FN','N','EMAIL','TEL','ORG','TITLE','REV','RELATED','X-ABRELATEDNAMES','X-ABLABEL'}
    lines=[];begins=ends=0
    for line,_ in vobject.base.getLogicalLines(io.StringIO(raw.lstrip('\ufeff')),allowQP=True):
        match=re.match(r'^(?:[A-Za-z0-9_-]+\.)?([A-Za-z0-9_-]+)(?=[;:])',line)
        if not match:continue
        name=match[1].upper().replace('_','-')
        if name in ('BEGIN','END'):
            if line.upper() not in ('BEGIN:VCARD','END:VCARD'):raise ValueError('Ungültige Kontaktstruktur')
            if name=='BEGIN':begins+=1
            else:ends+=1
            lines.append(line)
        elif name in allowed:lines.append(line)
    if begins!=1 or ends!=1 or not lines or lines[0].upper()!='BEGIN:VCARD' or lines[-1].upper()!='END:VCARD':
        raise ValueError('Unvollständige Kontaktstruktur')
    card=vobject.readOne('\r\n'.join(lines)+'\r\n',allowQP=True)
    if card.name!='VCARD':raise ValueError('Keine Kontaktkarte')
    def values(key):return [str(line.value)[:300] for line in card.contents.get(key,[])][:20]
    name=values('fn');uid=values('uid')
    organization=card.contents.get('org',[])
    parts=organization[0].value if organization else []
    if not isinstance(parts,(list,tuple)):parts=[parts]
    org=' / '.join(str(part) for part in parts)
    identifier=hashlib.sha256((scope+'\n'+(uid[0] if uid else raw)).encode()).hexdigest()
    related=[]
    for key in ('related','x-abrelatednames'):
        for line in card.contents.get(key,[])[:20]:
            labels=list(line.params.get('TYPE',[]))
            for label in card.contents.get('x-ablabel',[]):
                if getattr(label,'group',None)==getattr(line,'group',None) and getattr(line,'group',None):labels.append(str(label.value))
            related.append({'target':str(line.value)[:300],'type':','.join(labels)[:100]})
    return {'uid_key':hashlib.sha256((uid[0] if uid else '').removeprefix('urn:uuid:').encode()).hexdigest(),'related':related,'id':'icloud_contacts:'+identifier,'name':name[0] if name else org[:300],'emails':values('email'),'phones':values('tel'),'organization':org[:500],'job_title':(values('title') or [''])[0],'modified':(values('rev') or [''])[0][:100]}


def self_profile_question(question):
    return bool(re.search(r'mein(?:e[nrs])?\s+(?:aktueller?\s+)?(?:arbeitgeber|firma|position|beruf|kontaktkarte)|wo\s+arbeite\s+ich|bei\s+wem\s+arbeite\s+ich|wo\s+bin\s+ich\s+angestellt',question,re.I))


def wants_calendar(question):
    if self_profile_question(question) and not re.search(r'arbeitszeit|termin|kalender|wann|morgen|heute',question,re.I):return False
    return outdoor_question(question) or bool(re.search(r'kalender|termin|besprechung|zeit habe|habe ich zeit|frei|verfügbar|verfuegbar|arbeitszeit|wann arbeite|wann muss ich arbeiten|hab(?:e)? ich.{0,30}zeit|muss ich.{0,25}arbeiten|arbeite ich|was steht.{0,20}\ban\b|wie sieht.{0,20}(?:tag|woche|plan)',question,re.I))


def wants_contacts(question,sender=''):
    return bool(sender or re.search(r'kontakt|telefon|rufnummer|handy.?nummer|e.?mail.?adresse|arbeitgeber|wo arbeite|bei wem arbeite|wer ist|meine frau|mein mann|partner|meine position|mein beruf|meine firma',question,re.I))


class PersonalSources:
    def __init__(self,settings,store):
        self.settings,self.store=settings,store
        self.locks={kind:asyncio.Lock() for kind in ('calendar','contacts')};self.status={};self.retry={}

    async def refresh(self,kind,start=None,end=None,zone='Europe/Berlin',force=False):
        if not getattr(self.settings,'icloud_'+kind+'_enabled'):return None
        settings=self.settings.model_copy();scope=account_scope(settings,kind)
        async with self.locks[kind]:
            previous=self.store.dav_snapshot(scope)
            key='icloud_'+kind
            current=bool(previous)
            if current and kind=='calendar':
                try:current=datetime.fromisoformat(previous['value']['start'])<=start and datetime.fromisoformat(previous['value']['end'])>=end and previous['value'].get('zone')==zone
                except (KeyError,TypeError,ValueError):current=False
            if current and not force and scope not in self.retry and time.time()-previous['checked']<(60 if kind=='calendar' else 300):
                self.status[key]={'checked':datetime.fromtimestamp(previous['checked'],ZoneInfo('UTC')).isoformat(),'fetched':len(previous['value']['records']),'cached':True,'running':False}
                return {**previous['value'],'checked':previous['checked'],'stale':False}
            key='icloud_'+kind
            if self.retry.get(scope,0)>time.monotonic():
                self.status[key]={**self.status.get(key,{}),'error':self.status.get(key,{}).get('error','iCloud-Abruf fehlgeschlagen; erneuter Versuch in Kürze.')}
                return {**previous['value'],'checked':previous['checked'],'stale':True} if previous else None
            previous_status={'checked':datetime.fromtimestamp(previous['checked'],ZoneInfo('UTC')).isoformat(),'fetched':len(previous['value']['records'])} if previous else {}
            self.status[key]={**previous_status,'running':True}
            try:
                dav=DAV(settings,kind)
                data=await asyncio.wait_for(dav.read(settings,start,end,zone),timeout=60)
                if kind=='calendar':data.update(start=start.isoformat(),end=end.isoformat(),zone=zone)
                self.store.save_dav_snapshot(scope,data)
                self.retry.pop(scope,None)
                self.status[key]={'checked':datetime.now(ZoneInfo('UTC')).isoformat(),'fetched':len(data['records']),'running':False}
                return {**data,'checked':time.time(),'stale':False}
            except asyncio.CancelledError:
                self.status[key]={**previous_status,'running':False,'error':'iCloud-Abruf abgebrochen oder Zeitlimit erreicht. Bisherige Daten bleiben erhalten.'}
                raise
            except Exception as exc:
                self.retry[scope]=time.monotonic()+60
                self.status[key]={**previous_status,'error':icloud_error(exc,dav.stage if 'dav' in locals() else 'Konfiguration'),'running':False}
                return {**previous['value'],'checked':previous['checked'],'stale':True} if previous else None

    async def poll(self):
        now=datetime.now(ZoneInfo('Europe/Berlin'));start=now.replace(hour=0,minute=0,second=0,microsecond=0);end=start+timedelta(days=14)
        await asyncio.gather(self.refresh('calendar',start,end),self.refresh('contacts'))

    async def context(self,question,sender='',identity=None,zone='Europe/Berlin',requested=()):
        need_calendar=wants_calendar(question) or 'calendar' in requested;need_contacts=self_profile_question(question) or wants_contacts(question,sender) or 'contacts' in requested or bool(need_calendar and identity)
        result={'sources':[],'diagnostics':[],'calendar':None,'contacts':[],'self_contact':None}
        today=datetime.now(ZoneInfo(zone)).date();day=target_day(question,today)
        start=datetime.combine(day or today,daytime.min,ZoneInfo(zone));end=start+timedelta(days=1 if day else 14)
        calendar,contacts=await asyncio.gather(self.refresh('calendar',start,end,zone,force=bool(re.search(r'aktuell|jetzt|live|neu laden',question,re.I))) if need_calendar else asyncio.sleep(0),self.refresh('contacts',force=self_profile_question(question) or bool(re.search(r'aktuell|live|neu laden|meine frau|mein mann',question,re.I))) if need_contacts else asyncio.sleep(0))
        for kind,data,needed in [('calendar',calendar,need_calendar),('contacts',contacts,need_contacts)]:
            enabled=getattr(self.settings,'icloud_'+kind+'_enabled')
            if needed and enabled and (not data or data.get('stale')):result['diagnostics'].append('iCloud '+('Kalender' if kind=='calendar' else 'Kontakte')+': aktueller Abruf nicht vollständig verfügbar. Cache-Daten sind kein Live-Stand.')
        own_card=next((row for row in contacts['records'] if row['id']==identity),None) if contacts and identity else None
        own_emails={address.casefold() for address in own_card['emails']} if own_card and not contacts['stale'] else set()
        if calendar:
            matching=[row for row in calendar['records'] if datetime.fromisoformat(row['start'])<end and (datetime.fromisoformat(row['end'])>start or (row['start']==row['end'] and datetime.fromisoformat(row['start'])>=start))]
            matching=[{**row,'user_participation':[person['status'] for person in row.get('attendees',[]) if person['email'].casefold() in own_emails]} for row in matching]
            matching.sort(key=lambda row:row['start'])
            result['calendar']={'events':[{**row,'description':row.get('description','')[:500],'attendees':row.get('attendees',[])[:5]} for row in matching[:30]],'truncated':len(matching)>30,'checked':calendar['checked'],'stale':calendar['stale'],'start':start.isoformat(),'end':end.isoformat(),'collections':calendar['collections'],'cached_window':{'start':calendar.get('start'),'end':calendar.get('end')},'note':'Nur ausgewählte/eingebundene Kalender. Keine Einträge bedeuten nicht automatisch Freizeit oder feste Arbeitszeiten. Geteilte Kalender enthalten auch Termine anderer Personen. OPAQUE/busy bedeutet Kalenderbelegung, nicht automatisch persönliche Teilnahme. user_participation gilt nur für Mailadressen der bestätigten eigenen Kontaktkarte; DECLINED ist keine Zusage. Ganztägige Termine beachten; Enddatum exklusiv.'}
            result['sources'].append({'id':'icloud_calendar:window:'+hashlib.sha256((account_scope(self.settings,'calendar')+start.isoformat()).encode()).hexdigest(),'source':'calendar','title':'iCloud-Kalenderabruf','excerpt':json.dumps({k:v for k,v in result['calendar'].items() if k!='events'},ensure_ascii=False),'document_date':start.isoformat(),'url':None})
            for row in matching[:20]:
                result['sources'].append({'id':row['id'],'source':'calendar','title':row['title'],'excerpt':json.dumps(row,ensure_ascii=False)+('\nVERALTETER CACHE' if calendar['stale'] else ''),'document_date':row['start'],'url':None})
        if contacts:
            rows=contacts['records'];own=next((row for row in rows if row['id']==identity),None) if identity else None
            if own and self_profile_question(question):result['self_contact']={**own,'checked':contacts['checked'],'stale':contacts['stale'],'basis':'Vom angemeldeten Nutzer ausdrücklich als eigene Kontaktkarte ausgewählt; Firma/Position sind Angaben dieser Karte, keine unabhängig bestätigte Beschäftigung.'}
            tokens={word for word in re.findall(r'[\w@.+-]{3,}',(sender+' '+question).casefold()) if word not in STOPWORDS}
            selected=[]
            for row in rows:
                hay=(row['name']+' '+' '.join(row['emails'])+' '+row['organization']).casefold()
                if (sender and sender.casefold() in hay) or any(word in hay for word in tokens):selected.append(row)
            if own and self_profile_question(question) and own not in selected:selected.insert(0,own)
            result['contacts']=[{**row,'checked':contacts['checked'],'stale':contacts['stale']} for row in selected[:10]]
            if own and re.search(r'meine frau|mein mann|ehefrau|ehemann|ehepartner',question,re.I):
                candidates=[]
                for relation in own.get('related',[]):
                    if not re.search(r'spouse|ehepartner|ehefrau|ehemann',relation['type'],re.I):continue
                    target=relation['target'].casefold();uid_key=hashlib.sha256(relation['target'].removeprefix('urn:uuid:').encode()).hexdigest()
                    candidates.extend(row for row in rows if target==row['name'].casefold() or target in [email.casefold() for email in row['emails']] or (target.startswith('urn:uuid:') and row.get('uid_key')==uid_key))
                candidates=list({row['id']:row for row in candidates}.values())
                if len(candidates)==1:
                    partner=candidates[0];result['contacts']=[{**partner,'checked':contacts['checked'],'stale':contacts['stale']}];result['relationship_basis']='Explizite Ehepartner-Zuordnung in der bestätigten eigenen Kontaktkarte; keine Ableitung aus Namen.'
                    if not contacts['stale']:result['sender_aliases']=partner['emails'][:3]
            if sender and not result.get('sender_aliases'):
                matches=[row for row in rows if sender.casefold() in (row['name']+' '+' '.join(row['emails'])).casefold()]
                if len(matches)==1 and not contacts['stale']:result['sender_aliases']=matches[0]['emails'][:3]
            for row in result['contacts']:
                result['sources'].append({'id':row['id'],'source':'contacts','title':row['name'],'excerpt':json.dumps(row,ensure_ascii=False),'document_date':None,'url':None})
        return result
