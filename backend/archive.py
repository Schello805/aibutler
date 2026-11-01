"""Resumable read-only IMAP backfill. Batches are indexed locally, never sent to an LLM."""
import email
from contextlib import contextmanager
import hashlib
import imaplib
import re
import time
from email import policy
from email.utils import parsedate_to_datetime,parseaddr
from datetime import timezone


@contextmanager
def mailbox_connection(settings):
    box=imaplib.IMAP4_SSL(settings.imap_host,settings.imap_port,timeout=20)
    try:yield box
    finally:
        # A failed LOGOUT must not turn committed batches into failed imports.
        try:
            logout=getattr(box,'logout',None)
            if logout:logout()
        except (OSError,imaplib.IMAP4.error,EOFError):
            try:box.shutdown()
            except Exception:pass


def folders(box,configured):
    status,rows=box.list()
    if status!='OK':return [configured]
    result=[]
    for row in rows or []:
        if not row:continue
        text=row.decode('ascii',errors='surrogateescape')
        match=re.match(r'\((.*?)\)\s+(?:"[^"]*"|NIL)\s+(.+)$',text)
        if not match or '\\noselect' in match[1].lower():continue
        name=match[2]
        if name.startswith('"') and name.endswith('"'):
            name=name[1:-1].replace('\\"','"').replace('\\\\','\\')
        result.append(name)
    return list(dict.fromkeys([configured]+result))


def decode_part(part):
    try:return part.get_content()
    except (UnicodeError,LookupError):
        raw=part.get_payload(decode=True) or b''
        return raw.decode('utf-8',errors='replace')


def mail_item(raw,item_id):
    msg=email.message_from_bytes(raw,policy=policy.default)
    parts=[p for p in msg.walk() if p.get_content_disposition()!='attachment' and p.get_content_type()=='text/plain']
    html_only=not parts
    if not parts:parts=[p for p in msg.walk() if p.get_content_disposition()!='attachment' and p.get_content_type()=='text/html']
    body='\n'.join(decode_part(p) for p in parts)
    if html_only:
        from .retrieval import message_text
        body=message_text(body)
    try:
        date=parsedate_to_datetime(str(msg.get('Date','')))
        if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
        sent_at=date.astimezone(timezone.utc).isoformat()
    except (ValueError,TypeError):sent_at=''
    name,address=parseaddr(str(msg.get('From','')))
    return {'id':item_id,'version':'1','title':str(msg.get('Subject','Ohne Betreff'))[:500],'body':body,'source':'imap','sender_name':name,'sender_address':address,'sent_at':sent_at}


def backfill_batch(settings,store,batch_size=25):
    if not settings.imap_host:return {'configured':False}
    account=hashlib.sha256((settings.imap_host+'/'+settings.imap_user).encode()).hexdigest()[:16]
    results=[];remaining_budget=batch_size
    known=store.mail_ids()
    with mailbox_connection(settings) as box:
        box.login(settings.imap_user,settings.imap_password)
        names=folders(box,settings.imap_folder) if settings.imap_archive_all_folders else [settings.imap_folder]
        rotation=int(store.sync_state('mail-archive-cursor:'+account).get('index',0))%max(1,len(names))
        names=names[rotation:]+names[:rotation]
        for name in names:
            key='mail-archive:'+account+':'+name
            progress={'folder':name,'total':0,'indexed':0,'remaining':0,'errors':0,'checked':time.time()}
            try:
                mailbox='"'+name.replace('\\','\\\\').replace('"','\\"')+'"'
                status,_=box.select(mailbox,readonly=True)
                if status!='OK':raise RuntimeError('folder unavailable')
                validity=box.response('UIDVALIDITY')[1][0]
                if not validity:raise RuntimeError('missing UIDVALIDITY')
                identity=hashlib.sha256(f'{settings.imap_host}/{settings.imap_user}/{name}'.encode()).hexdigest()[:16]
                prefix=f'mail:{identity}:{validity.decode()}:'
                status,data=box.uid('search',None,'ALL')
                if status!='OK':raise RuntimeError('UID search failed')
                uids=data[0].split()
                missing=[uid for uid in reversed(uids) if prefix+uid.decode() not in known]
                # The normal worker owns recent arrivals and their proactive relevance checks.
                # Backfill must not race ahead and silently consume those notifications.
                from datetime import datetime,timedelta
                cutoff=(datetime.now()-timedelta(days=settings.imap_lookback_days)).strftime('%d-%b-%Y')
                recent_status,recent_data=box.uid('search',None,'SINCE',cutoff)
                if recent_status!='OK':raise RuntimeError('recent inventory unavailable')
                recent=set(recent_data[0].split())
                historical=[uid for uid in missing if uid not in recent]
                progress.update(total=len(uids),indexed=len(uids)-len(missing),remaining=len(missing))
                for uid in historical[:min(5,remaining_budget)]:
                    remaining_budget-=1
                    try:
                        status,parts=box.uid('fetch',uid,'(BODY.PEEK[])')
                        if status!='OK':raise RuntimeError('fetch failed')
                        raw=next(p[1] for p in parts if isinstance(p,tuple))
                        item=mail_item(raw,prefix+uid.decode())
                        store.commit_item(item)  # no finding, notification or external inference
                        known.add(item['id'])
                        progress['indexed']+=1;progress['remaining']-=1
                    except Exception:progress['errors']+=1
                progress['complete']=progress['remaining']==0 and progress['errors']==0
            except Exception:
                progress['error']='Ordner nicht vollständig abrufbar; erneuter Versuch folgt.'
                progress['complete']=False
            store.save_sync_state(key,progress)
            results.append(progress)
            # Other folders still get their inventory counted even when the fetch budget is spent.
    store.save_sync_state('mail-archive-cursor:'+account,{'index':rotation+max(1,batch_size//5)})
    return {'configured':True,'folders':results,'complete':all(r.get('complete') for r in results),'errors':sum(r.get('errors',0)+bool(r.get('error')) for r in results),'indexed':sum(r['indexed'] for r in results),'total':sum(r['total'] for r in results)}
