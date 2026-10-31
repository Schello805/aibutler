"""Read-only ntfy replay into the local timeline, with durable per-topic cursors."""
import hashlib
import json
from datetime import datetime,timezone
from urllib.parse import urlsplit
import httpx


def history_urls(settings):
    raw=settings.ntfy_history_urls.strip()
    return list(dict.fromkeys(line.strip().rstrip('/') for line in raw.splitlines() if line.strip())) if raw else ([settings.ntfy_url.rstrip('/')] if settings.ntfy_url else [])


def validate_history_url(url,settings):
    parts=urlsplit(url);base=urlsplit(settings.ntfy_url)
    if parts.scheme not in ('http','https') or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment or not parts.path.strip('/'):
        raise ValueError('ntfy benötigt vollständige Topic-URLs ohne Query oder eingebettete Zugangsdaten')
    if settings.ntfy_token and (parts.scheme,parts.netloc)!=(base.scheme,base.netloc):
        raise ValueError('History-Topics müssen zur konfigurierten ntfy-Instanz gehören')


async def sync_ntfy(settings,store):
    import asyncio
    urls=history_urls(settings)
    async with httpx.AsyncClient(timeout=20,follow_redirects=False) as client:
        async def read_topic(url):
            topic_hash=hashlib.sha256(url.encode()).hexdigest()[:20]
            key='ntfy:'+topic_hash
            previous=store.sync_state(key)
            since=previous.get('cursor','all')
            try:
                validate_history_url(url,settings)
                headers={'Authorization':'Bearer '+settings.ntfy_token} if settings.ntfy_token else {}
                imported=0;cursor=since;truncated=previous.get('truncated',False)
                for attempt in range(2):
                    async with client.stream('GET',url+'/json',params={'poll':'1','since':since},headers=headers) as response:
                        if response.status_code==400 and since!='all' and attempt==0:
                            since='all';continue  # The old message cursor may have expired server-side.
                        response.raise_for_status()
                        truncated=truncated or response.headers.get('X-Messages-Truncated')=='1'
                        total_bytes=0
                        async for line in response.aiter_lines():
                            total_bytes+=len(line.encode())
                            if total_bytes>12*1024*1024:raise ValueError('ntfy replay exceeds safe batch size')
                            if not line.strip():continue
                            event=json.loads(line)
                            if event.get('event')!='message':continue
                            cursor=event['id']
                            text=event.get('message','')
                            if text.startswith('Neuer relevanter Vorgang im lokalen Butler.') or ('im lokalen Butler. Details in der App.' in text):continue
                            if float(event.get('time',0))>datetime.now(timezone.utc).timestamp():continue
                            date=datetime.fromtimestamp(event['time'],timezone.utc).isoformat()
                            item={'id':'ntfy:'+topic_hash+':'+event['id'],'version':'1','title':event.get('title') or 'ntfy-Ereignis','body':text,'source':'ntfy','document_date':date}
                            if not store.unchanged(item):store.commit_item(item);imported+=1
                    break
                state={'cursor':cursor,'checked':datetime.now(timezone.utc).isoformat(),'imported':imported,'truncated':truncated,'retention_note':'Nur serverseitig noch verfügbare Meldungen; lokale Kopien bleiben erhalten.'}
            except Exception as exc:
                code=exc.response.status_code if isinstance(exc,httpx.HTTPStatusError) else None
                state={**previous,'error':'Topic-Verlauf nicht abrufbar'+(f' (HTTP {code})' if code else '')+'. URL/Leseberechtigung prüfen.','checked':datetime.now(timezone.utc).isoformat()}
            store.save_sync_state(key,state)
            return state
        results=await asyncio.gather(*(read_topic(url) for url in urls))
    return {'configured':bool(urls),'topics':len(urls),'imported':sum(r.get('imported',0) for r in results),'errors':sum(bool(r.get('error')) for r in results),'truncated':any(r.get('truncated',False) for r in results),'history_available':any(not r.get('error') for r in results)}
