"""Account-scoped local memories. Only direct user text can propose new knowledge."""
from datetime import date,datetime,timezone,timedelta
import hashlib
import re
import uuid
from fastapi import APIRouter,Depends,HTTPException,Request
from pydantic import BaseModel,Field,model_validator
from typing import Literal
from zoneinfo import ZoneInfo

KINDS=('preference','project','fact')

def today():return datetime.now(ZoneInfo('Europe/Berlin')).date().isoformat()
def stamp():return datetime.now(timezone.utc).isoformat()
def fingerprint(text):return hashlib.sha256(' '.join(text.casefold().split()).encode()).hexdigest()
def usable(row):return row['status']=='active' and (not row['valid_from'] or row['valid_from']<=today()) and (not row['valid_until'] or row['valid_until']>=today())
def words(text):return set(re.findall(r'[a-zäöüß]{4,}',text.casefold()))-{'meine','meinen','einen','einem','dass','bitte','möchte','gerade','immer','heute','morgen','welche','welchen'}

def classify(text):
    if slot(text) in ('employer','home','work_hours') or re.search(r'\b(?:arbeite|wohne|arbeitgeber|arbeitszeiten)\b',text,re.I):return 'fact'
    if re.search(r'antworte|antworten|sprich|bevorzuge|lieblings|ich mag',text,re.I):return 'preference'
    if re.match(r'(?:ich|wir) (?:möchte\w*|plane\w*|will|wollen|baue\w*)\b',text,re.I):return 'project'
    return 'fact'

def slot(text):
    patterns={
        'employer':r'(?:ich arbeite|arbeite ich)\b.{0,100}\b(?:bei|für)\b|ich\b.{0,100}\b(?:bei|für)\b.{0,100}\barbeite\b|(?:mein )?arbeitgeber\s*(?:ist|heißt|:)',
        'home':r'ich wohne|wohne ich|ich\s+in\b.{0,120}\bwohne\b|mein wohnort',
        'work_hours':r'arbeitszeiten|(?:ich arbeite|arbeite ich) (?:von|zwischen|montags)',
    }
    matches=[key for key,pattern in patterns.items() if re.search(pattern,text,re.I)]
    if matches:return matches[0] if len(matches)==1 else ''
    favorites=set(match.casefold() for match in re.findall(r'\blieblings[a-zäöüß]+\b',text,re.I))
    if favorites:return next(iter(favorites)) if len(favorites)==1 else ''
    length=bool(re.search(r'\b(?:kurz|knapp|ausführlich|lang|detailliert)\b',text,re.I))
    language=bool(re.search(r'\b(?:deutsch|englisch|französisch|spanisch)\b',text,re.I))
    if re.search(r'antworte|antworten|sprich',text,re.I):
        if length and not language:return 'response_length'
        if language and not length:return 'response_language'
    return ''

def explicit_memory(text):
    return re.match(r'^(?:bitte\s+)?(?:merk(?:e)? dir|merke|speichere(?: dir)?(?: bitte)?(?: als erinnerung)?|erinnere dich daran)(?:\s*[:,-]\s*|\s+)(.+)$',text,re.I|re.S)

def candidate(text):
    if re.search(r'\b(?:wissen|erfahren|hilf|erkläre|zeig|suche|suchst|kannst|gib)\b',text,re.I):return False
    if re.search(r'dass du',text,re.I) and classify(text)!='preference':return False
    # Deliberately conservative: no extraction from quoted documents or third parties.
    return len(text)<=2000 and '?' not in text and bool(re.match(r'^(?:(?:ich|wir) (?:möchte\w*|plane\w*|will|wollen|baue\w*)\b|ich (?:arbeite (?:nicht mehr )?bei|arbeite (?:nicht mehr )?für|wohne|bevorzuge|mag)\b|mein(?:e)? (?:arbeitgeber|wohnort|arbeitszeiten|lieblings\w*)\b|bitte (?:antworte|sprich)\b)',text,re.I))

def sensitive(text):
    return bool(re.search(r'(?:mein|das|der)\s+(?:passwort|api.?key|token|pin)\s*(?:ist|lautet|:|=)|\bsk-[a-zA-Z0-9_-]{20,}',text,re.I))


def start_date(text):
    match=re.search(r'\bab(?: dem)?\s+(\d{4}-\d{2}-\d{2}|\d{1,2}\.\d{1,2}\.\d{4})\b',text,re.I)
    if match:
        try:return date.fromisoformat(match[1]) if '-' in match[1] else datetime.strptime(match[1],'%d.%m.%Y').date()
        except ValueError:return None
    if re.search(r'\bab (?:heute|jetzt|sofort)\b',text,re.I):return date.fromisoformat(today())
    if re.search(r'\bab morgen\b',text,re.I):return date.fromisoformat(today())+timedelta(days=1)
    return None

def needs_date(row):
    return bool(row['slot'] and not row['valid_from'] and re.search(r'\bab (?:nächst|naechst|januar|februar|märz|maerz|april|mai|juni|juli|august|september|oktober|november|dezember|sommer|herbst|winter|früh|spaeter|später|\d)',row['content'],re.I))

class MemoryInput(BaseModel):
    content:str=Field(min_length=2,max_length=2000)
    kind:Literal['preference','project','fact']='fact'
    valid_from:date|None=None
    valid_until:date|None=None
    @model_validator(mode='after')
    def valid_dates(self):
        self.content=self.content.strip()
        if len(self.content)<2:raise ValueError('Bitte einen Inhalt eingeben')
        if self.valid_from and self.valid_until and self.valid_until<self.valid_from:raise ValueError('Enddatum liegt vor dem Beginn')
        if sensitive(self.content):raise ValueError('Zugangsdaten bitte unter Verbindungen hinterlegen, nicht als Erinnerung')
        return self

class Memories:
    def __init__(self,store):
        self.store=store
        with store.connect() as db:
            db.executescript('''CREATE TABLE IF NOT EXISTS memories(
            id TEXT PRIMARY KEY,owner TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,
            slot TEXT NOT NULL,status TEXT NOT NULL,digest TEXT NOT NULL,source TEXT NOT NULL,
            conversation_id TEXT,created TEXT NOT NULL,updated TEXT NOT NULL,
            valid_from TEXT,valid_until TEXT,revision INTEGER NOT NULL DEFAULT 1,source_date TEXT);
            CREATE INDEX IF NOT EXISTS memory_owner_status ON memories(owner,status);
            CREATE INDEX IF NOT EXISTS memory_owner_digest ON memories(owner,digest);''')
            if 'source_date' not in {row['name'] for row in db.execute('PRAGMA table_info(memories)')}:db.execute('ALTER TABLE memories ADD COLUMN source_date TEXT')

    def list(self,owner):
        with self.store.connect() as db:return [dict(row) for row in db.execute('SELECT * FROM memories WHERE owner=? ORDER BY updated DESC,id',(owner,))]

    def get(self,owner,identifier):
        with self.store.connect() as db:
            row=db.execute('SELECT * FROM memories WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            return dict(row) if row else None

    def conflicts(self,owner,row):
        if not row['slot']:return []
        with self.store.connect() as db:return [dict(r) for r in db.execute("SELECT * FROM memories WHERE owner=? AND slot=? AND id!=? AND status='active' AND (valid_until IS NULL OR valid_until>=?)",(owner,row['slot'],row['id'],today()))]

    def create(self,owner,value,source='manual',conversation_id=None,pending=False,source_date=None):
        value=MemoryInput.model_validate(value)
        if value.valid_from is None:value.valid_from=start_date(value.content)
        if value.valid_from and value.valid_until and value.valid_until<value.valid_from:raise ValueError('Enddatum liegt vor dem Beginn.')
        digest=fingerprint(value.content)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute("SELECT * FROM memories WHERE owner=? AND digest=? AND status IN ('active','pending')",(owner,digest)).fetchone()
            if row:
                # A fresh direct restatement confirms the same content again; an old
                # historical suggestion must not make old facts appear freshly stated.
                if row['status']=='active' and source=='chat' and source_date is None:
                    db.execute('UPDATE memories SET updated=?,source_date=?,conversation_id=?,revision=revision+1 WHERE owner=? AND id=?',(stamp(),stamp(),conversation_id,owner,row['id']))
                    row=db.execute('SELECT * FROM memories WHERE owner=? AND id=?',(owner,row['id'])).fetchone()
                return dict(row),False
            if db.execute('SELECT count(*) FROM memories WHERE owner=?',(owner,)).fetchone()[0]>=500:raise ValueError('Bitte zuerst alte Erinnerungen unter Mein Wissen entfernen (maximal 500).')
            identifier=uuid.uuid4().hex;now=stamp();key=slot(value.content)
            conflict=key and db.execute("SELECT 1 FROM memories WHERE owner=? AND slot=? AND status='active' AND (valid_until IS NULL OR valid_until>=?)",(owner,key,today())).fetchone()
            status='pending' if pending or conflict or needs_date({'slot':key,'content':value.content,'valid_from':value.valid_from}) else 'active'
            if status=='pending' and db.execute("SELECT count(*) FROM memories WHERE owner=? AND status='pending'",(owner,)).fetchone()[0]>=50:raise ValueError('Bitte zuerst offene Vorschläge unter Mein Wissen prüfen (maximal 50).')
            db.execute('INSERT INTO memories VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(identifier,owner,value.kind,value.content,key,status,digest,source,conversation_id,now,now,value.valid_from.isoformat() if value.valid_from else None,value.valid_until.isoformat() if value.valid_until else None,1,source_date or now))
        return self.get(owner,identifier),True

    def confirm(self,owner,identifier):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM memories WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if not row:raise KeyError()
            if row['status']=='active':return dict(row)
            if row['status']!='pending':raise ValueError('Dieser Vorschlag ist nicht mehr offen.')
            if needs_date(row):raise ValueError('Ab welchem Datum gilt das? Bitte unter Mein Wissen zuerst Gültig ab ergänzen.')
            # The explicit confirmation replaces conflicting assertions atomically.
            if row['slot']:
                others=list(db.execute("SELECT * FROM memories WHERE owner=? AND slot=? AND id!=? AND status='active'",(owner,row['slot'],identifier)))
                for old in others:
                    if row['valid_from'] and row['valid_from']>today() and (not old['valid_from'] or old['valid_from']<row['valid_from']):
                        end=(date.fromisoformat(row['valid_from'])-timedelta(days=1)).isoformat()
                        if old['valid_until']:end=min(end,old['valid_until'])
                        db.execute('UPDATE memories SET valid_until=?,updated=?,revision=revision+1 WHERE owner=? AND id=?',(end,stamp(),owner,old['id']))
                    else:db.execute("UPDATE memories SET status='superseded',updated=?,revision=revision+1 WHERE owner=? AND id=?",(stamp(),owner,old['id']))
            db.execute("UPDATE memories SET status='active',updated=?,revision=revision+1 WHERE owner=? AND id=?",(stamp(),owner,identifier))
        return self.get(owner,identifier)

    def update(self,owner,identifier,value,revision):
        value=MemoryInput.model_validate(value)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT * FROM memories WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if not row:raise KeyError()
            if row['revision']!=revision:raise ValueError('Die Erinnerung wurde auf einem anderen Gerät geändert. Bitte neu laden.')
            key=slot(value.content)
            if key and row['status']=='active' and db.execute("SELECT 1 FROM memories WHERE owner=? AND slot=? AND id!=? AND status='active'",(owner,key,identifier)).fetchone():raise ValueError('Dazu gibt es bereits eine aktive Angabe. Bitte den Widerspruch zuerst klären.')
            db.execute("UPDATE memories SET kind=?,content=?,slot=?,digest=?,updated=?,source='manual-edit',source_date=?,valid_from=?,valid_until=?,revision=revision+1 WHERE owner=? AND id=?",(value.kind,value.content,key,fingerprint(value.content),stamp(),stamp(),value.valid_from.isoformat() if value.valid_from else None,value.valid_until.isoformat() if value.valid_until else None,owner,identifier))
        return self.get(owner,identifier)

    def archive(self,owner,identifier):
        with self.store.connect() as db:
            if not db.execute("UPDATE memories SET status='archived',updated=?,revision=revision+1 WHERE owner=? AND id=?",(stamp(),owner,identifier)).rowcount:raise KeyError()
        return self.get(owner,identifier)

    def delete(self,owner,identifier):
        with self.store.connect() as db:
            if not db.execute('DELETE FROM memories WHERE owner=? AND id=?',(owner,identifier)).rowcount:raise KeyError()

    def reject(self,owner,identifier):
        with self.store.connect() as db:
            row=db.execute('SELECT status FROM memories WHERE owner=? AND id=?',(owner,identifier)).fetchone()
            if not row:raise KeyError()
            if row['status']!='pending':raise ValueError('Der Vorschlag wurde bereits bearbeitet. Änderungen sind unter Mein Wissen möglich.')
            db.execute("DELETE FROM memories WHERE owner=? AND id=? AND status='pending'",(owner,identifier))
        return {'rejected':True}

    def retrieve(self,owner,question):
        if not owner:return []
        terms=words(question);ranked=[]
        topics={'employer':r'arbeitgeber|wo arbeite|firma|beruf|angestellt','home':r'wohnort|wo wohne|zuhause','work_hours':r'arbeitszeit|wann arbeite|zeit habe|habe ich zeit','response_style':r'antwort|sprache|vorliebe'}
        for row in self.list(owner):
            if not usable(row):continue
            score=len(terms&words(row['content']))
            if row['kind']=='preference':score+=2
            if row['slot'] in topics and re.search(topics[row['slot']],question,re.I):score+=5
            if row['kind']=='project' and re.search(r'draußen|draussen|vorhaben|projekt|was.{0,20}tun|arbeiten',question,re.I):score+=3
            if score:
                days=(datetime.now(timezone.utc)-datetime.fromisoformat(row['updated'])).days
                ranked.append((score,{'id':row['id'],'kind':row['kind'],'content':row['content'],'slot':row['slot'],'updated':row['updated'],'source':row['source'],'source_date':row['source_date'],'valid_from':row['valid_from'],'valid_until':row['valid_until'],'needs_check':row['kind'] in ('fact','project') and days>=(30 if row['kind']=='project' else 90),'basis':'Ausdrücklich gespeicherte Nutzerangabe, keine unabhängige Bestätigung.'}))
        selected=[];size=0
        for _,row in sorted(ranked,key=lambda pair:(pair[0],pair[1]['updated']),reverse=True):
            if size+len(row['content'])>8000:continue
            selected.append(row);size+=len(row['content'])
            if len(selected)==8:break
        return selected

    def propose(self,owner,text,conversation_id,source_date=None):
        if not owner or not candidate(text) or sensitive(text):return None
        row,created=self.create(owner,{'content':text,'kind':classify(text)},'chat',conversation_id,pending=True,source_date=source_date)
        return self.proposal(owner,row) if created else None

    def proposal(self,owner,row):
        return {'id':row['id'],'content':row['content'],'kind':row['kind'],'needs_date':needs_date(row),'conflicts':[{'id':r['id'],'content':r['content']} for r in self.conflicts(owner,row)]}

    def chat(self,owner,text,conversation_id,last):
        explicit=explicit_memory(text)
        proposal=last.get('memory_proposal')
        answer=None;extra={}
        if explicit:
            if not owner:answer='Bitte melde dich mit deinem Benutzerkonto an, damit ich die Erinnerung dir zuordnen kann.'
            elif sensitive(explicit[1]):answer='Zugangsdaten bitte unter Verbindungen hinterlegen, nicht als Erinnerung.'
            elif not 2<=len(explicit[1].strip())<=2000:answer='Bitte formuliere die Erinnerung mit 2 bis 2000 Zeichen.'
            else:
                try:row,_=self.create(owner,{'content':explicit[1],'kind':classify(explicit[1])},'chat',conversation_id)
                except ValueError as exc:answer=str(exc)
                else:
                    if row['status']=='pending' and not needs_date(row) and not self.conflicts(owner,row):row=self.confirm(owner,row['id'])
                    if row['status']=='pending':
                        extra['memory_proposal']=self.proposal(owner,row)
                        answer='Ab welchem Datum gilt das? Bitte ergänze Gültig ab unter „Mein Wissen“.' if needs_date(row) else 'Dazu habe ich eine andere Angabe gespeichert. Soll die neue Angabe die bisherige ersetzen?'
                    else:answer='Gemerkt. Du findest das unter „Mein Wissen“.';extra['memory_saved']=row['id']
        elif owner and proposal and re.fullmatch(r'(ja(?: bitte)?|merken|speichern|übernehmen)[.! ]*',text,re.I):
            try:
                if proposal.get('action')=='archive':self.archive(owner,proposal['id']);answer='Als erledigt markiert. Das Vorhaben zählt nicht mehr als offen.'
                else:self.confirm(owner,proposal['id']);answer='Gemerkt. Ich berücksichtige das künftig.'
            except KeyError:answer='Dieser Vorschlag ist nicht mehr offen. Bitte prüfe „Mein Wissen“.'
            except ValueError as exc:answer=str(exc);extra['memory_proposal']=proposal
        elif owner and proposal and re.fullmatch(r'(nein(?: danke)?|nicht merken|nicht speichern)[.! ]*',text,re.I):
            row=self.get(owner,proposal['id'])
            if proposal.get('action')=='archive':return {'answer':'Ich ändere am Status des Vorhabens nichts.','sources':[],'diagnostics':[],'external_used':False,'answer_mode':'local'}
            if row and row['status']=='pending':self.reject(owner,row['id'])
            answer='Diese Erinnerung ist bereits bestätigt. Du kannst sie unter Mein Wissen löschen.' if row and row['status']=='active' else 'Ich übernehme das nicht ins Langzeitgedächtnis.'
        elif owner and re.search(r'\b(?:ist|sind)\b.{0,40}\b(?:fertig|erledigt|abgeschlossen)\b|\b(?:habe|haben)\b.{0,80}\b(?:abgeschlossen|fertiggestellt)\b',text,re.I) and not re.search(r'\b(?:nicht|morgen|bald|will|möchte)\b',text,re.I):
            matches=[row for row in self.list(owner) if usable(row) and row['kind']=='project' and words(text)&words(row['content'])]
            if len(matches)==1:
                row=matches[0];extra['memory_proposal']={'id':row['id'],'content':row['content'],'kind':'project','action':'archive','conflicts':[]}
                answer='Soll ich dieses Vorhaben als erledigt markieren?'
        elif owner and candidate(text) and not sensitive(text) and not re.search(r'hilf|kannst du|erkläre|such(?:e)? mir|wie geht|was muss',text,re.I):
            try:row,_=self.create(owner,{'content':text,'kind':classify(text)},'chat',conversation_id,pending=True)
            except ValueError as exc:answer=str(exc)
            else:
                if row['status']=='active':answer='Das habe ich bereits unter „Mein Wissen“ gespeichert.'
                else:
                    extra['memory_proposal']=self.proposal(owner,row)
                    answer='Dazu habe ich eine andere Angabe gespeichert. Soll die neue Angabe die bisherige ersetzen?' if extra['memory_proposal']['conflicts'] else 'Soll ich mir das für spätere Gespräche merken?'
        elif re.search(r'^(?:was (?:weißt|weisst|wei.t) du über mich|was hast du (?:dir )?gemerkt|welche erinnerungen)',text,re.I):
            rows=[row for row in self.list(owner) if usable(row)] if owner else []
            answer='Gespeichert ist: '+'; '.join(row['content'][:180] for row in rows[:8])+'. Weitere Einträge findest du unter „Mein Wissen“.' if rows else 'Ich habe noch keine bestätigten Erinnerungen für dich. Du kannst mit „Merk dir …“ beginnen.'
        return {'answer':answer,'sources':[],'diagnostics':[],'external_used':False,'answer_mode':'local','ai_issue':None,**extra} if answer else None

    def discover(self,owner):
        with self.store.connect() as db:
            rows=list(db.execute("SELECT m.text,m.conversation_id,m.created FROM conversation_messages m JOIN conversations c ON c.id=m.conversation_id WHERE c.owner_id=? AND m.role='user' ORDER BY m.id DESC LIMIT 500",(owner,)))
        count=0
        for row in rows:
            if count>=30:break
            expression=explicit_memory(row['text']);text=expression[1] if expression else row['text']
            if sensitive(text) or not 2<=len(text)<=2000:continue
            try:
                if expression:
                    _,created=self.create(owner,{'content':text,'kind':classify(text)},'chat',row['conversation_id'],pending=True,source_date=row['created'])
                    proposed=created
                else:proposed=self.propose(owner,text,row['conversation_id'],row['created'])
            except ValueError:break
            if proposed:count+=1
        return count


def memory_router(memory,auth):
    router=APIRouter(prefix='/api/memories',dependencies=[Depends(auth)])
    def owner(request):
        if not request.state.user:raise HTTPException(403,'Mit einem Benutzerkonto anmelden')
        return request.state.user['id']
    def perform(function,*args,**kwargs):
        try:return function(*args,**kwargs)
        except KeyError:raise HTTPException(404,'Erinnerung nicht gefunden')
        except ValueError as exc:raise HTTPException(409,str(exc))
    @router.get('')
    def listing(request:Request):
        user=owner(request);rows=memory.list(user)
        return {'memories':[{**row,'needs_date':needs_date(row),'conflicts':memory.proposal(user,row)['conflicts'] if row['status']=='pending' else []} for row in rows]}
    @router.post('')
    def create(value:MemoryInput,request:Request):return perform(memory.create,owner(request),value)[0]
    class EditMemory(MemoryInput):revision:int=Field(ge=1)
    @router.put('/{identifier}')
    def edit(identifier:str,value:EditMemory,request:Request):return perform(memory.update,owner(request),identifier,value,value.revision)
    @router.post('/discover')
    def discover(request:Request):return {'proposed':perform(memory.discover,owner(request))}
    @router.post('/{identifier}/confirm')
    def confirm(identifier:str,request:Request):return perform(memory.confirm,owner(request),identifier)
    @router.post('/{identifier}/reject')
    def reject(identifier:str,request:Request):return perform(memory.reject,owner(request),identifier)
    @router.post('/{identifier}/archive')
    def archive(identifier:str,request:Request):return perform(memory.archive,owner(request),identifier)
    @router.delete('/{identifier}')
    def delete(identifier:str,request:Request):perform(memory.delete,owner(request),identifier);return {'deleted':True}
    return router
