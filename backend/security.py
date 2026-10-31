"""Bound requests before JSON parsing and attach browser security policies."""
import asyncio
from starlette.responses import JSONResponse

JSON_LIMIT=1024*1024
AUDIO_LIMIT=4*1024*1024
CSP="; ".join((
    "default-src 'none'", "script-src 'self'", "style-src 'self'",
    "img-src 'self' data:", "font-src 'self'", "connect-src 'self'",
    "media-src 'self' blob:", "worker-src 'self'", "manifest-src 'self'",
    "object-src 'none'", "base-uri 'none'", "form-action 'self'", "frame-ancestors 'none'",
))

class SecurityMiddleware:
    def __init__(self,app):self.app=app

    async def __call__(self,scope,receive,send):
        if scope['type']!='http':return await self.app(scope,receive,send)
        async def secure_send(message):
            if message['type']=='http.response.start':
                headers=list(message.get('headers',[]))
                headers.extend([
                    (b'content-security-policy',CSP.encode()),
                    (b'x-frame-options',b'DENY'),
                    (b'x-content-type-options',b'nosniff'),
                    (b'referrer-policy',b'no-referrer'),
                    (b'permissions-policy',b'microphone=(self), camera=(), geolocation=(), payment=(), usb=()'),
                    (b'x-dns-prefetch-control',b'off'),
                ])
                if scope.get('scheme')=='https':
                    headers.append((b'strict-transport-security',b'max-age=31536000'))
                if scope['path'].startswith('/api') or scope['path']=='/':
                    headers.append((b'cache-control',b'no-store'))
                message={**message,'headers':headers}
            await send(message)
        async def reject(code,detail):
            await JSONResponse({'detail':detail},status_code=code)(scope,receive,secure_send)
        if scope['path'].startswith('/api/') and scope['method'] in ('POST','PUT','PATCH','DELETE'):
            limit=AUDIO_LIMIT if scope['path']=='/api/speech/transcribe' else JSON_LIMIT
            lengths=[v for k,v in scope['headers'] if k.lower()==b'content-length']
            if lengths:
                try:
                    if len(lengths)!=1 or not lengths[0].isdigit():raise ValueError
                    length=int(lengths[0])
                except ValueError:return await reject(400,'Ungültige Anfragegröße')
                if length>limit:return await reject(413,'Anfrage ist zu groß')
            # Do not trust Content-Length: chunked/understated bodies are bounded too.
            chunks=[];size=0
            try:
                async with asyncio.timeout(30):
                    while True:
                        message=await receive()
                        if message['type']=='http.disconnect':return
                        chunk=message.get('body',b'');size+=len(chunk)
                        if size>limit:return await reject(413,'Anfrage ist zu groß')
                        chunks.append(chunk)
                        if not message.get('more_body',False):break
            except TimeoutError:return await reject(408,'Zeitlimit beim Empfangen der Anfrage')
            body=b''.join(chunks);del chunks
            delivered=False
            async def bounded_receive():
                nonlocal delivered
                if not delivered:
                    delivered=True
                    return {'type':'http.request','body':body,'more_body':False}
                return await receive()
            return await self.app(scope,bounded_receive,secure_send)
        await self.app(scope,receive,secure_send)
