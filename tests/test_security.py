import asyncio
import os
import httpx
from fastapi.testclient import TestClient
from backend.main import create_app
from backend.config import Settings
from backend.security import SecurityMiddleware,JSON_LIMIT,AUDIO_LIMIT
from backend.store import Store


def app_for(path):
    return create_app(Settings(_env_file=None,data_dir=path,api_token='test',worker_enabled=False))


def test_browser_policies_also_cover_errors_and_private_pages(tmp_path):
    with TestClient(app_for(tmp_path),base_url='https://testserver') as client:
        for path in ('/','/api/status','/not-found','/static/app.js'):
            response=client.get(path)
            policy=response.headers['content-security-policy']
            assert "script-src 'self'" in policy and "frame-ancestors 'none'" in policy
            assert "'unsafe-inline'" not in policy and "'unsafe-eval'" not in policy
            assert response.headers['x-frame-options']=='DENY'
            assert 'camera=()' in response.headers['permissions-policy']
            assert response.headers['strict-transport-security']=='max-age=31536000'
        assert client.get('/api/status').headers['cache-control']=='no-store'
        assert client.get('/').headers['cache-control']=='no-store'
        for path in ('/docs','/redoc','/openapi.json','/static/../.env'):
            assert client.get(path).status_code==404
    with TestClient(app_for(tmp_path)) as client:
        assert 'strict-transport-security' not in client.get('/').headers


def test_body_budget_cannot_be_bypassed_with_missing_or_false_length():
    async def run(path,chunks,headers):
        reached=[];output=[]
        async def app(scope,receive,send):
            reached.append((await receive())['body'])
            await send({'type':'http.response.start','status':200,'headers':[]})
            await send({'type':'http.response.body','body':b'ok'})
        messages=iter([{'type':'http.request','body':chunk,'more_body':n<len(chunks)-1} for n,chunk in enumerate(chunks)])
        async def receive():return next(messages)
        async def send(message):output.append(message)
        await SecurityMiddleware(app)({'type':'http','path':path,'method':'POST','scheme':'http','headers':headers},receive,send)
        return output[0]['status'],reached,output
    for headers in ([],[(b'content-length',b'1')]):
        code,reached,output=asyncio.run(run('/api/session',[b'x'*JSON_LIMIT,b'overflow'],headers))
        assert code==413 and not reached
        assert b'content-security-policy' in dict(output[0]['headers'])
    code,reached,_=asyncio.run(run('/api/chat',[b'hello',b'world'],[]))
    assert code==200 and reached==[b'helloworld']
    assert asyncio.run(run('/api/chat',[],[(b'content-length',str(JSON_LIMIT+1).encode())]))[0]==413
    assert asyncio.run(run('/api/chat',[],[(b'content-length',b'-1')]))[0]==400
    assert asyncio.run(run('/api/speech/transcribe',[b'x'*(JSON_LIMIT+1)],[]))[0]==200
    assert asyncio.run(run('/api/speech/transcribe',[],[(b'content-length',str(AUDIO_LIMIT+1).encode())]))[0]==413


def test_private_database_permissions_repaired_on_open(tmp_path):
    root=tmp_path/'private';store=Store(root)
    os.chmod(root,0o755);os.chmod(store.path,0o644)
    Store(root)
    assert root.stat().st_mode & 0o777==0o700
    assert store.path.stat().st_mode & 0o777==0o600


def test_parallel_chat_research_is_bounded_and_slots_are_released(tmp_path,monkeypatch):
    app=app_for(tmp_path)
    async def run():
        started=asyncio.Event();release=asyncio.Event();count=0
        async def lighting(_):
            nonlocal count
            count+=1
            if count==2:started.set()
            await release.wait()
            return {'fresh':True,'lights':[],'checked':'2026-10-31'}
        monkeypatch.setattr(app.state.worker,'lighting_context',lighting)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers={'Authorization':'Bearer test'}) as client:
            tasks=[asyncio.create_task(client.post('/api/chat',json={'message':'Welche Lichter sind gerade an?'})) for _ in range(2)]
            try:
                await asyncio.wait_for(started.wait(),3)
                assert (await client.post('/api/chat',json={'message':'Welche Lichter sind gerade an?'})).status_code==429
            finally:release.set()
            responses=await asyncio.gather(*tasks)
            assert all(r.status_code==200 for r in responses)
            assert (await client.post('/api/chat',json={'message':'Welche Lichter sind gerade an?'})).status_code==200
    asyncio.run(run())


def test_credentials_cannot_be_embedded_in_connection_urls(tmp_path):
    with TestClient(app_for(tmp_path)) as client:
        for service,field in (('paperless','paperless_url'),('ntfy','ntfy_url')):
            for address in ('https://user:private-password@example.com/topic','https://example.com/topic?token=private-password','https://example.com/\npath'):
                response=client.put('/api/connections/'+service,headers={'Authorization':'Bearer test'},json={field:address})
                assert response.status_code==422
                assert 'private-password' not in response.text
