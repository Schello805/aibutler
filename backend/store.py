"""Local chunk retrieval using SQLite FTS5; no embedding downloads or cloud index."""
import json
import os
import re
import sqlite3
import uuid
from .retrieval import query_terms, evidence_excerpt,relevant_evidence, evidence_recency, account_activity_kind, message_text
from pathlib import Path

class Store:
    def __init__(self, root: Path):
        root.mkdir(parents=True, exist_ok=True)
        root.chmod(0o700)
        self.path = root / 'butler.sqlite3'
        descriptor=os.open(self.path,os.O_CREAT|os.O_RDWR,0o600)
        os.close(descriptor)
        self.path.chmod(0o600)
        with self.connect() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,title TEXT,updated TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE TABLE IF NOT EXISTS conversation_messages(id INTEGER PRIMARY KEY,conversation_id TEXT,role TEXT,text TEXT,payload TEXT,created TEXT DEFAULT CURRENT_TIMESTAMP);
            CREATE INDEX IF NOT EXISTS conversation_order ON conversation_messages(conversation_id,id);
            CREATE TABLE IF NOT EXISTS smart_history(id INTEGER PRIMARY KEY,scope TEXT,state_id TEXT,name TEXT,value TEXT,status TEXT,checked TEXT,source_ts TEXT);
            CREATE INDEX IF NOT EXISTS smart_history_latest ON smart_history(scope,state_id,id DESC);
            CREATE TABLE IF NOT EXISTS public_cache(key TEXT PRIMARY KEY,value TEXT,checked REAL);
            CREATE TABLE IF NOT EXISTS sync_state(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS mail_metadata(item_id TEXT PRIMARY KEY,sender_name TEXT,sender_address TEXT,sent_at TEXT);
            CREATE TABLE IF NOT EXISTS item_metadata(item_id TEXT PRIMARY KEY, document_date TEXT, filename TEXT);
            CREATE TABLE IF NOT EXISTS notification_state(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS smart_snapshots(scope TEXT PRIMARY KEY, checked TEXT, states TEXT);
            CREATE TABLE IF NOT EXISTS items(id TEXT PRIMARY KEY, version TEXT, title TEXT, body TEXT, source TEXT);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(item_id UNINDEXED, title, body);
            CREATE TABLE IF NOT EXISTS context(id INTEGER PRIMARY KEY CHECK(id=1), value TEXT);
            INSERT OR IGNORE INTO context VALUES(1, '{"projects":[]}');
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, item_id TEXT, title TEXT, reason TEXT, sent INTEGER DEFAULT 0, created TEXT DEFAULT CURRENT_TIMESTAMP);
            ''')
            if 'owner_id' not in {row['name'] for row in db.execute('PRAGMA table_info(conversations)')}:
                db.execute('ALTER TABLE conversations ADD COLUMN owner_id TEXT')
            if db.execute("INSERT OR IGNORE INTO notification_state VALUES('quiet-policy-v1','1')").rowcount:
                # Preserve all findings, but retire the old noisy notification backlog once.
                db.execute('UPDATE events SET sent=2 WHERE sent=0')

    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def public_cache(self,key):
        with self.connect() as db:
            row=db.execute('SELECT value,checked FROM public_cache WHERE key=?',(key,)).fetchone()
            return {'value':json.loads(row['value']),'checked':row['checked']} if row else None

    def save_public_cache(self,key,value):
        import time
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO public_cache VALUES(?,?,?)',(key,json.dumps(value),time.time()))
            db.execute('DELETE FROM public_cache WHERE checked<?',(time.time()-32*86400,))
            db.execute('DELETE FROM public_cache WHERE key NOT IN (SELECT key FROM public_cache ORDER BY checked DESC LIMIT 1000)')

    def activity_context(self):
        # Bounded, local recall of actual requests and progress reports, never a new AI profile.
        expression=' OR '.join('"'+word+'"*' for word in ('bau','hühner','garten','streich','repar','renov','erricht','montier','schuppen','zaun','terrass'))
        with self.connect() as db:
            rows=db.execute("SELECT i.id AS item_id,i.title,i.body,i.source,m.document_date FROM items i LEFT JOIN item_metadata m ON m.item_id=i.id WHERE i.source IN ('imap','paperless') AND i.id IN (SELECT item_id FROM chunks WHERE chunks MATCH ?) ORDER BY m.document_date DESC,i.rowid DESC LIMIT 200",(expression,))
            result=[]
            for row in rows:
                hit=dict(row)
                if re.search(r'newsletter|rabatt|sonderangebot|stellenangebot|werbung',hit['title'],re.I):continue
                text=message_text(hit['body'])
                text=re.split(r'(?im)^\s*(?:Am .{8,100}schrieb|On .{8,100}wrote|Von:|From:|-----Original|>{1,})',text)[0]
                if not re.search(r'kannst du|könntest du|würdest du|bitte|ich möchte|wir möchten|wir sollten|fertig gebaut|bereits erledigt|abgesagt',text,re.I):continue
                hit['body']=text[:3000]
                hit['activity_status']='completed_or_cancelled' if re.search(r'fertig gebaut|bereits erledigt|abgesagt|nicht mehr nötig',text,re.I) else 'request_status_unknown'
                result.append(hit)
                if len(result)==4:break
            return result

    def context(self):
        with self.connect() as db:
            return json.loads(db.execute('SELECT value FROM context WHERE id=1').fetchone()[0])

    def save_context(self, value):
        with self.connect() as db:
            db.execute('UPDATE context SET value=? WHERE id=1', (json.dumps(value),))

    def unchanged(self, item):
        with self.connect() as db:
            row = db.execute('SELECT version FROM items WHERE id=?', (item['id'],)).fetchone()
            return row is not None and row[0] == item['version']

    def set_item_metadata(self,item):
        if item.get('source')=='imap' and 'sender_address' in item:
            with self.connect() as db:
                db.execute('INSERT OR REPLACE INTO mail_metadata VALUES(?,?,?,?)',(item['id'],item.get('sender_name',''),item.get('sender_address',''),item.get('sent_at','')))
        if not item.get('document_date'):
            return
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO item_metadata VALUES(?,?,?)',(item['id'],item['document_date'],item.get('filename','')))

    def commit_item(self, item, reason=None, notify=True):
        self.set_item_metadata(item)
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO items VALUES(?,?,?,?,?)', tuple(item[k] for k in ('id','version','title','body','source')))
            db.execute('DELETE FROM chunks WHERE item_id=?', (item['id'],))
            for start in range(0, max(1, len(item['body'])), 1000):
                db.execute('INSERT INTO chunks VALUES(?,?,?)', (item['id'], item['title'], item['body'][start:start+1200]))
            if reason:
                db.execute('INSERT INTO events(item_id,title,reason,sent) VALUES(?,?,?,?)', (item['id'],item['title'],reason,0 if notify else 2))

    def mail_metadata_ids(self):
        with self.connect() as db:
            return {r[0] for r in db.execute('SELECT item_id FROM mail_metadata')}

    def sender_messages(self,name):
        with self.connect() as db:
            rows=db.execute("SELECT i.id AS item_id,i.title,i.body,i.source,m.sender_name,m.sender_address,m.sent_at AS document_date FROM items i JOIN mail_metadata m ON m.item_id=i.id WHERE lower(m.sender_name || ' ' || m.sender_address) LIKE ? ORDER BY m.sent_at DESC,i.rowid DESC LIMIT 5",('%'+name.lower()+'%',))
            return [dict(row) for row in rows]

    def recent_account_activity(self):
        with self.connect() as db:
            rows=db.execute("SELECT i.id AS item_id,i.title,i.body,i.source,m.sent_at AS document_date,m.sender_name,m.sender_address FROM items i LEFT JOIN mail_metadata m ON m.item_id=i.id WHERE i.source='imap' AND (lower(i.title) LIKE '%login%' OR lower(i.title) LIKE '%sign%in%' OR lower(i.title) LIKE '%anmeld%' OR lower(i.title) LIKE '%registrier%' OR lower(i.title) LIKE '%willkommen%' OR lower(i.title) LIKE '%welcome%' OR lower(i.title) LIKE '%account created%') ORDER BY m.sent_at DESC,i.rowid DESC LIMIT 100")
            groups={'login':[],'signup':[]}
            for row in rows:
                hit=dict(row);kind=account_activity_kind(hit)
                if not kind or len(groups[kind])>=3:continue
                hit['body']=message_text(hit['body'])[:6000];hit['activity_kind']=kind;groups[kind].append(hit)
            # Preserve both meanings of "angemeldet" when a question is ambiguous.
            result=[]
            for index in range(3):
                for group in groups.values():
                    if index<len(group):result.append(group[index])
            return result[:5]

    def recent_purchases(self):
        # Prefer actual order confirmations over shipping updates, advertising and payouts.
        with self.connect() as db:
            rows=db.execute("SELECT i.id AS item_id,i.title,i.body,i.source,m.sent_at AS document_date,m.sender_name,m.sender_address FROM items i LEFT JOIN mail_metadata m ON m.item_id=i.id WHERE i.source='imap' AND (i.title LIKE 'Bestellt:%' OR lower(i.title) LIKE '%bestellbestätigung%' OR lower(i.title) LIKE '%bestellung bestätigt%' OR lower(i.title) LIKE '%order confirmation%') ORDER BY m.sent_at DESC,i.rowid DESC LIMIT 5")
            return [dict(row) for row in rows]

    def mail_ids(self):
        with self.connect() as db:
            return {r[0] for r in db.execute("SELECT id FROM items WHERE source='imap'")}

    def search(self, query, exclude='', strict=True, source_filter=None,recent=False):
        terms=query_terms(query)
        if not terms:
            return []
        # Require terms across the whole document, not necessarily the same chunk.
        # Rank short excerpts afterwards, reserving space for each matching source.
        expression=' OR '.join(terms)
        conditions=''.join(' AND c.item_id IN (SELECT item_id FROM chunks WHERE chunks MATCH ?)' for _ in terms) if strict else ''
        with self.connect() as db:
            sources=[r[0] for r in db.execute('SELECT DISTINCT source FROM items ORDER BY source')]
            groups=[]
            for source in sources:
                if source_filter is not None and source not in source_filter:continue
                ordering='(SELECT document_date FROM item_metadata WHERE item_id=c.item_id) DESC,rank' if recent else 'rank'
                sql='SELECT c.item_id,c.title,c.body,i.source FROM chunks c JOIN items i ON i.id=c.item_id WHERE chunks MATCH ? AND c.item_id != ? AND i.source=?'+conditions+' ORDER BY '+ordering
                params=[expression,exclude,source]+(terms if strict else [])
                hits,seen=[],set()
                for row in db.execute(sql,params):
                    if row['item_id'] in seen:
                        continue
                    seen.add(row['item_id'])
                    full=db.execute('SELECT body FROM items WHERE id=?',(row['item_id'],)).fetchone()[0]
                    if not relevant_evidence(query,row['title'],full):
                        continue
                    hit=dict(row);hit['body']=evidence_excerpt(full,query,row['body'])
                    meta=db.execute('SELECT document_date,filename FROM item_metadata WHERE item_id=?',(row['item_id'],)).fetchone()
                    if meta:
                        hit.update(dict(meta))
                    hits.append(hit)
                    if len(hits)==50:
                        break
                if hits:
                    if recent or source=='ntfy' or (strict and re.search(r'wartung|gewartet',query,re.I)):
                        hits.sort(key=evidence_recency,reverse=True)
                    groups.append(hits[:5])
            # Round-robin keeps relevant mail and Paperless evidence together.
            result=[]
            for index in range(5):
                for group in groups:
                    if index<len(group):
                        result.append(group[index])
                        if len(result)==5:
                            return result
            return result

    def events(self, pending=False):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM events WHERE item_id NOT LIKE 'iobroker:%' "+('AND sent=0 ' if pending else '')+'ORDER BY id DESC LIMIT 100')]

    def mark_sent(self, event_id):
        with self.connect() as db:
            db.execute('UPDATE events SET sent=1 WHERE id=?', (event_id,))

    def smart_snapshot(self, scope):
        with self.connect() as db:
            row=db.execute('SELECT checked,states FROM smart_snapshots WHERE scope=?',(scope,)).fetchone()
            return {'checked':row['checked'],'states':json.loads(row['states'])} if row else {'checked':None,'states':[]}

    def save_smart_snapshot(self, scope, states, checked):
        self.record_smart_states(scope,states,checked)
        with self.connect() as db:
            row=db.execute('SELECT states FROM smart_snapshots WHERE scope=?',(scope,)).fetchone()
            previous={s['id']:s for s in json.loads(row['states'])} if row else {}
            current={s['id']:s for s in states}
            # Only a complete successful poll can mark a previously returned state as absent.
            for state_id,old in previous.items():
                if state_id not in current:
                    current[state_id]={**old,'status':'missing','value':None}
            values=sorted(current.values(),key=lambda s:s['id'])
            db.execute('INSERT OR REPLACE INTO smart_snapshots VALUES(?,?,?)',(scope,checked,json.dumps(values,ensure_ascii=False)))

    def notification_value(self,key,default=''):
        with self.connect() as db:
            row=db.execute('SELECT value FROM notification_state WHERE key=?',(key,)).fetchone()
            return row[0] if row else default

    def set_notification_value(self,key,value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO notification_state VALUES(?,?)',(key,str(value)))

    def pending_notifications(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM events WHERE sent=0 AND item_id NOT LIKE 'iobroker:%' ORDER BY id LIMIT 1000")]

    def settle_notifications(self, ids, sent=False):
        with self.connect() as db:
            db.executemany('UPDATE events SET sent=? WHERE id=?',[(1 if sent else 2,event_id) for event_id in ids])

    def save_sync_state(self,key,value):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO sync_state VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))

    def sync_state(self,key):
        with self.connect() as db:
            row=db.execute('SELECT value FROM sync_state WHERE key=?',(key,)).fetchone()
            return json.loads(row[0]) if row else {}

    def sync_overview(self):
        with self.connect() as db:
            states=[{'key':r[0],**json.loads(r[1])} for r in db.execute('SELECT key,value FROM sync_state')]
            counts={r[0]:r[1] for r in db.execute('SELECT source,count(*) FROM items GROUP BY source')}
        return {'states':states,'indexed':counts}

    def record_smart_states(self, scope, states, checked):
        with self.connect() as db:
            for state in states:
                value=json.dumps(state['value'],ensure_ascii=False,sort_keys=True)
                last=db.execute('SELECT value,status FROM smart_history WHERE scope=? AND state_id=? ORDER BY id DESC LIMIT 1',(scope,state['id'])).fetchone()
                if last and last[0]==value and last[1]==state['status']:continue
                db.execute('INSERT INTO smart_history(scope,state_id,name,value,status,checked,source_ts) VALUES(?,?,?,?,?,?,?)',(scope,state['id'],state['name'],value,state['status'],checked,str(state.get('source_ts',''))))

    def smart_history_context(self,scope,queries):
        words=list(dict.fromkeys(word.casefold() for query in queries for word in re.findall(r'\w{3,}',query)))[:15]
        if not words:return []
        with self.connect() as db:
            clauses=' OR '.join("lower(state_id || ' ' || name) LIKE ?" for _ in words)
            rows=db.execute('SELECT state_id,name,value,status,checked,source_ts FROM smart_history WHERE scope=? AND ('+clauses+') ORDER BY id DESC LIMIT 30',[scope]+['%'+word+'%' for word in words])
            return [dict(row) for row in rows]

    def coverage_summary(self):
        overview=self.sync_overview()
        archives=[state for state in overview['states'] if state['key'].startswith('mail-archive:')]
        topics=[state for state in overview['states'] if state['key'].startswith('ntfy:')]
        return {'indexed':overview['indexed'],'mail_archive_started':bool(archives),'mail_archive_complete':bool(archives) and all(state.get('complete',False) for state in archives),'mail_archive_remaining':sum(state.get('remaining',0) for state in archives),'ntfy_topics_synced':sum(not state.get('error') for state in topics),'ntfy_topics_failed':sum(bool(state.get('error')) for state in topics),'ntfy_cache_replay_ok':bool(topics) and all(not state.get('error') and not state.get('truncated') for state in topics),'ntfy_retention':'Nur zum ersten Abruf noch auf dem Server gespeicherte Meldungen und anschließend lokal archivierte Ereignisse; frühere gelöschte Daten fehlen.','smart_history':'Lokale Zustandsänderungen erst seit Einrichtung erfasst; aktuelle Zustände sind keine vollständige Historie.'}

    def conversations(self,owner_id=None):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT id,title,updated FROM conversations'+(' WHERE owner_id=?' if owner_id else '')+' ORDER BY updated DESC,rowid DESC LIMIT 50',(owner_id,) if owner_id else ())]

    def conversation(self,conversation_id,owner_id=None):
        with self.connect() as db:
            row=db.execute('SELECT id,title,updated FROM conversations WHERE id=?'+(' AND owner_id=?' if owner_id else ''),(conversation_id,owner_id) if owner_id else (conversation_id,)).fetchone()
            if not row:return None
            messages=[{**dict(message),'payload':json.loads(message['payload'])} for message in db.execute('SELECT role,text,payload,created FROM (SELECT * FROM conversation_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 100) ORDER BY id',(conversation_id,))]
            return {**dict(row),'messages':messages}

    def save_exchange(self,conversation_id,question,result,owner_id=None):
        conversation_id=conversation_id or uuid.uuid4().hex
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO conversations(id,title,owner_id) VALUES(?,?,?)',(conversation_id,question[:80],owner_id))
            if owner_id and not db.execute('SELECT 1 FROM conversations WHERE id=? AND owner_id=?',(conversation_id,owner_id)).fetchone():raise ValueError('Gespräch nicht zugänglich')
            db.execute('INSERT INTO conversation_messages(conversation_id,role,text,payload) VALUES(?,?,?,?)',(conversation_id,'user',question,'{}'))
            db.execute('INSERT INTO conversation_messages(conversation_id,role,text,payload) VALUES(?,?,?,?)',(conversation_id,'assistant',result['answer'],json.dumps(result,ensure_ascii=False)))
            db.execute('UPDATE conversations SET updated=CURRENT_TIMESTAMP WHERE id=?',(conversation_id,))
        return conversation_id
