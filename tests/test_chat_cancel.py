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
            request_id='a'*32;body={'message':'Was sollte ich beachten?','share_external':True,'request_id':request_id}
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


def test_disconnected_request_continues_and_can_be_recovered_without_resubmit(tmp_path,monkeypatch):
    started=asyncio.Event();release=asyncio.Event()
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,icloud_calendar_enabled=True))
    monkeypatch.setattr(app.state.accounts,'session',lambda token:{'id':token,'role':'member','email':'test@example.invalid'} if token else None)
    calls=[]
    async def calendar(*args,**kwargs):
        calls.append('calendar');started.set();await release.wait()
        return {'calendar':{'events':[],'stale':False},'sources':[],'diagnostics':[]}
    monkeypatch.setattr(app.state.worker.personal,'context',calendar)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            headers={'cookie':'butler_session=a','origin':'http://127.0.0.1'}
            body={'message':'Meine nächsten Termine?','request_id':'e'*32}
            request=asyncio.create_task(client.post('/api/chat',json=body,headers=headers))
            await asyncio.wait_for(started.wait(),2)
            request.cancel()
            try:await request
            except asyncio.CancelledError:pass
            state=(await client.get('/api/chat/result/'+'e'*32,headers=headers)).json()
            assert state['status']=='pending' and state['progress']['phase']=='calendar'
            assert state['conversation_id']
            assert (await client.get('/api/chat/result/'+'e'*32,headers={**headers,'cookie':'butler_session=b'})).status_code==404
            assert (await client.post('/api/chat',json=body,headers=headers)).status_code==409
            release.set()
            for _ in range(100):
                state=(await client.get('/api/chat/result/'+'e'*32,headers=headers)).json()
                if state['status']=='done':break
                await asyncio.sleep(.01)
            assert state['status']=='done'
            assert state['result']['timings']['calendar']>=0
            assert calls==['calendar']
            conversation=app.state.store.conversation(state['conversation_id'],'a')
            assert len(conversation['messages'])==2
    asyncio.run(scenario())


def test_dialogue_does_not_wait_for_two_busy_research_slots(tmp_path,monkeypatch):
    entered=0;ready=asyncio.Event()
    async def slow(*args,**kwargs):
        nonlocal entered
        entered+=1
        if entered==2:ready.set()
        await asyncio.Event().wait()
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,chat_llm_enabled=True))
    monkeypatch.setattr('backend.main.plan_question',slow)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            headers={'Authorization':'Bearer test','Origin':'http://127.0.0.1'}
            pending=[asyncio.create_task(client.post('/api/chat',headers=headers,json={'message':'Was sollte ich beachten?','request_id':letter*32,'share_external':True})) for letter in ('a','b')]
            await asyncio.wait_for(ready.wait(),2)
            response=await asyncio.wait_for(client.post('/api/chat',headers=headers,json={'message':'Kannst du mich gut verstehen?'}),1)
            assert response.status_code==200 and response.json()['route']=='reply'
            for letter in ('a','b'):await client.post('/api/chat/cancel',headers=headers,json={'request_id':letter*32})
            assert [response.status_code for response in await asyncio.gather(*pending)]==[499,499]
    asyncio.run(scenario())
