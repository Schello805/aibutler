import email
import json
from email import policy
from email.utils import parsedate_to_datetime, parseaddr
from datetime import timezone
from datetime import datetime, timedelta
import hashlib
import re
import imaplib
from urllib.parse import urljoin, urlsplit, parse_qs
import httpx

def _read_mail(s, search_terms=None, limit=None, known_ids=None, sender=None):
    if not s.imap_host:
        return [], 0, False
    items = []
    header_only=False
    with imaplib.IMAP4_SSL(s.imap_host, s.imap_port, timeout=30) as box:
        box.login(s.imap_user, s.imap_password)
        status, _ = box.select(s.imap_folder, readonly=True)
        if status != 'OK':
            raise RuntimeError('Cannot select IMAP folder')
        validity = box.response('UIDVALIDITY')[1][0]
        if not validity:
            raise RuntimeError('Missing UIDVALIDITY')
        since = (datetime.now()-timedelta(days=s.imap_lookback_days)).strftime('%d-%b-%Y')
        if sender:
            quoted='"'+sender.replace('\\','\\\\').replace('"','\\"')+'"'
            if sender.isascii():
                status,data=box.uid('search',None,'FROM',quoted)
            else:
                status,data=box.uid('search','CHARSET','UTF-8','FROM',quoted.encode('utf-8'))
        elif search_terms:
            # Fixed server-side TEXT search, no date restriction. Terms are quoted data.
            groups=search_terms if isinstance(search_terms[0],list) else [search_terms]
            criteria=[]
            for group in groups:
                quoted=['"'+str(term).replace('\\','\\\\').replace('"','\\"')+'"' for term in group]
                expression=' '.join(['OR']*(len(quoted)-1)+[part for term in quoted for part in ['TEXT',term]])
                criteria.append('('+expression+')')
            status,data=box.uid('search',None,*criteria)
        else:
            status, data = box.uid('search', None, 'SINCE', since)
        if status != 'OK' and search_terms:
            # Some providers (e.g. IONOS) reject server-side full-text searches.
            # Prefer heating subjects, then maintenance subjects, and inspect bodies locally.
            header_only=True
            collected=[]
            for group in groups:
                criteria=['OR']*(len(group)-1)
                for term in group:
                    criteria.extend(['SUBJECT','"'+str(term).replace('\\','\\\\').replace('"','\\"')+'"'])
                status,data=box.uid('search',None,*criteria)
                if status!='OK':
                    raise RuntimeError('IMAP search failed')
                collected.extend(sorted(data[0].split(),key=int,reverse=True))
            uids=list(dict.fromkeys(collected))
        else:
            if status != 'OK':
                raise RuntimeError('IMAP search failed')
            uids=data[0].split()
        total=len(uids)
        account = hashlib.sha256(f'{s.imap_host}/{s.imap_user}/{s.imap_folder}'.encode()).hexdigest()[:16]
        # Newest UIDs first; known messages are already available in the local index.
        candidates=(uids[:limit] if header_only else list(reversed(uids))[:limit]) if limit else uids
        for uid in candidates:
            item_id=f'mail:{account}:{validity.decode()}:{uid.decode()}'
            if known_ids and item_id in known_ids:
                continue
            status, parts = box.uid('fetch', uid, '(BODY.PEEK[])')
            if status != 'OK':
                raise RuntimeError('IMAP fetch failed')
            raw = next(p[1] for p in parts if isinstance(p, tuple))
            msg = email.message_from_bytes(raw, policy=policy.default)
            bodies = []
            for part in msg.walk():
                if part.get_content_type() == 'text/plain' and part.get_content_disposition() != 'attachment':
                    bodies.append(part.get_content())
            # HTML-only mail is retained as inert text, never rendered as HTML.
            if not bodies:
                bodies = [p.get_content() for p in msg.walk() if p.get_content_type() == 'text/html' and p.get_content_disposition() != 'attachment']
            account = hashlib.sha256(f'{s.imap_host}/{s.imap_user}/{s.imap_folder}'.encode()).hexdigest()[:16]
            try:
                date=parsedate_to_datetime(str(msg.get('Date','')))
                if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                sent_at=date.astimezone(timezone.utc).isoformat()
            except (ValueError,TypeError):
                sent_at=''
            sender_name,sender_address=parseaddr(str(msg.get('From','')))
            items.append(dict(sender_name=sender_name,sender_address=sender_address,sent_at=sent_at,id=f'mail:{account}:{validity.decode()}:{uid.decode()}', version='1', title=str(msg.get('Subject','Ohne Betreff'))[:500], body='\n'.join(bodies)[:100000], source='imap', spam=str(msg.get('X-Spam-Flag','')).upper()=='YES', advertising=bool(msg.get('List-Unsubscribe'))))
    return items,total,header_only


def read_mail(s,known_ids=None):
    return _read_mail(s,known_ids=known_ids)[0]


def search_mail(s, terms, known_ids=None):
    items,total,header_only=_read_mail(s,search_terms=terms,limit=100,known_ids=known_ids)
    return {'items':items,'total':total,'limited':total>100,'subject_only':header_only}


def search_sender_mail(s,sender,known_ids=None):
    items,total,_=_read_mail(s,sender=sender,limit=5,known_ids=known_ids)
    return {'items':items,'total':total,'limited':total>5,'sender_search':True}


async def read_paperless(s):
    if not s.paperless_url:
        return []
    base = s.paperless_url.rstrip('/')+'/'
    url = urljoin(base, 'api/documents/?ordering=id&page_size=100')
    items, visited = [], set()
    instance = hashlib.sha256(base.encode()).hexdigest()[:12]
    async with httpx.AsyncClient(timeout=30) as client:
        while url:
            if urlsplit(url).netloc != urlsplit(base).netloc or urlsplit(url).scheme != urlsplit(base).scheme or url in visited:
                raise RuntimeError('Invalid Paperless pagination')
            visited.add(url)
            r = await client.get(url, headers={'Authorization':'Token '+s.paperless_token})
            r.raise_for_status()
            page = r.json()
            for doc in page['results']:
                title, body = doc.get('title',''), doc.get('content','') or ''
                version = hashlib.sha256((title+'\n'+body).encode()).hexdigest()
                items.append(dict(id=f'paperless:{instance}:{doc["id"]}', version=version, title=title, body=body[:200000], source='paperless', document_date=doc.get('created',''),filename=doc.get('original_file_name','')))
            if page.get('next'):
                # Reverse proxies may generate http/internal next URLs. Never follow them
                # with credentials: only extract the page number and keep the configured origin.
                next_page=parse_qs(urlsplit(page['next']).query).get('page',[''])[0]
                if not next_page.isdigit() or int(next_page)<1:
                    raise RuntimeError('Invalid Paperless page number')
                url=urljoin(base,'api/documents/?ordering=id&page_size=100&page='+next_page)
            else:
                url=None
    return items


async def search_paperless(s,queries):
    """Supplement the complete local index with fresh server-side full-text matches."""
    if not s.paperless_url:return []
    base=s.paperless_url.rstrip('/')+'/'
    instance=hashlib.sha256(base.encode()).hexdigest()[:12]
    result={}
    async with httpx.AsyncClient(timeout=20) as client:
        for query in queries[:2]:
            response=await client.get(base+'api/documents/',params={'query':query,'ordering':'-created','page_size':25},headers={'Authorization':'Token '+s.paperless_token})
            response.raise_for_status()
            for doc in response.json()['results']:
                title=doc.get('title','');body=doc.get('content','') or ''
                item={'id':f'paperless:{instance}:{doc["id"]}','version':hashlib.sha256((title+'\n'+body).encode()).hexdigest(),'title':title,'body':body,'source':'paperless','document_date':doc.get('created',''),'filename':doc.get('original_file_name','')}
                result[item['id']]=item
    return list(result.values())


async def paperless_document_text(s,item_id,cache_get,cache_save):
    """Fetch only an explicitly selected document, then OCR locally if content is missing."""
    import asyncio
    from .document_text import ocr_original,class_excerpt
    base=s.paperless_url.rstrip('/')+'/'
    instance=hashlib.sha256(base.encode()).hexdigest()[:12]
    match=re.fullmatch(r'paperless:'+instance+r':(\d+)',item_id)
    if not match:raise ValueError('Dokument gehört nicht zur aktuellen Paperless-Verbindung')
    headers={'Authorization':'Token '+s.paperless_token}
    async with httpx.AsyncClient(timeout=6,follow_redirects=False,trust_env=False) as client:
        response=await client.get(base+'api/documents/'+match[1]+'/',headers=headers)
        response.raise_for_status();document=response.json()
        if class_excerpt(document.get('content') or ''):return document['content'][:200000],False
        version=hashlib.sha256(('license-tsv-v1|'+str(document.get('modified',''))+'|'+str(document.get('checksum',''))+'|'+str(document.get('original_file_name',''))).encode()).hexdigest()
        cached=await asyncio.to_thread(cache_get,item_id,version)
        if cached is not None:return cached,True
        data=bytearray()
        async with client.stream('GET',base+'api/documents/'+match[1]+'/download/',params={'original':'true'},headers=headers) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data)>20*1024*1024:raise ValueError('Dokument zu groß für lokale Texterkennung')
    text=await asyncio.to_thread(ocr_original,bytes(data))
    if text:await asyncio.to_thread(cache_save,item_id,version,text)
    return text,True


async def paperless_selected_document(s,item_id):
    """Read extracted text for one already selected document; never download originals here."""
    base=s.paperless_url.rstrip('/')+'/'
    instance=hashlib.sha256(base.encode()).hexdigest()[:12]
    match=re.fullmatch(r'paperless:'+instance+r':(\d+)',item_id)
    if not s.paperless_url or not match:raise ValueError('Dokument gehört nicht zur aktuellen Paperless-Verbindung')
    async with httpx.AsyncClient(timeout=2.5,follow_redirects=False,trust_env=False) as client:
        async with client.stream('GET',base+'api/documents/'+match[1]+'/',headers={'Authorization':'Token '+s.paperless_token}) as response:
            response.raise_for_status();data=bytearray()
            async for part in response.aiter_bytes():
                data.extend(part)
                if len(data)>2*1024*1024:raise ValueError('Paperless-Dokumentantwort zu groß')
    doc=json.loads(data);body=(doc.get('content') or '')[:200000];title=doc.get('title') or ''
    return {'id':item_id,'title':title,'body':body,'source':'paperless','document_date':doc.get('created') or '','filename':doc.get('original_file_name') or '','version':hashlib.sha256((title+'\n'+body).encode()).hexdigest()}
