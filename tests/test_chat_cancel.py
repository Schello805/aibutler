import asyncio
import httpx
from backend.config import Settings
from backend.main import create_app


def test_cancellation_is_owner_scoped_and_releases_chat_capacity(tmp_path,monkeypatch):
    started=asyncio.Event();cancelled=asyncio.Event()
    async def slow_plan(*args,**kwargs):
        started.set()
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    monkeypatch.setattr('backend.main.plan_question',slow_plan)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True))
    monkeypatch.setattr(app.state.accounts,'session',lambda token:{'id':token,'role':'admin','email':'test@example.invalid'} if token else None)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            request_id='a'*32;body={'message':'Was steht an?','share_external':True,'request_id':request_id}
            headers={'cookie':'butler_session=owner-a','origin':'http://127.0.0.1'}
            running=asyncio.create_task(client.post('/api/chat',json=body,headers=headers))
            await asyncio.wait_for(started.wait(),2)
            saved=(await client.get('/api/conversations',headers=headers)).json()
            assert len(saved)==1
            messages=(await client.get('/api/conversations/'+saved[0]['id'],headers=headers)).json()['messages']
            assert messages[0]['text']==body['message'] and messages[1]['payload']['pending']
            wrong=await client.post('/api/chat/cancel',json={'request_id':request_id},headers={**headers,'cookie':'butler_session=owner-b'})
            assert wrong.json()=={'cancelled':False} and not running.done()
            result=await client.post('/api/chat/cancel',json={'request_id':request_id},headers=headers)
            assert result.json()=={'cancelled':True}
            assert (await asyncio.wait_for(running,2)).status_code==499
            assert cancelled.is_set()
            assert (await client.post('/api/chat/cancel',json={'request_id':request_id},headers=headers)).json()=={'cancelled':False}
            # A new request is accepted after cancellation; no leaked semaphore slot.
            started.clear();cancelled.clear();body['request_id']='b'*32
            second=asyncio.create_task(client.post('/api/chat',json=body,headers=headers));await asyncio.wait_for(started.wait(),2)
            await client.post('/api/chat/cancel',json={'request_id':body['request_id']},headers=headers)
            assert (await second).status_code==499
    asyncio.run(scenario())
