import asyncio
import json
import httpx
import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend.intelligence import generate_stream,generate,answer_sink
from backend.fast_chat import compact_payload


def config(tmp_path,provider='openai'):
    return Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,allow_external_llm=True,chat_llm_enabled=True,llm_api_key='synthetic-key',llm_model='test-model',llm_provider=provider,gemini_paid_confirmed=True)


@pytest.mark.parametrize('provider',['openai','gemini'])
def test_provider_stream_yields_visible_text_and_requires_completion(tmp_path,monkeypatch,provider):
    records=[{'type':'response.output_text.delta','delta':'Hallo '},{'type':'response.output_text.delta','delta':'Michael.'},{'type':'response.completed','response':{'status':'completed'}}] if provider=='openai' else [{'candidates':[{'content':{'parts':[{'thought':True,'text':'hidden'},{'text':'Hallo '}]}}]},{'candidates':[{'content':{'parts':[{'text':'Michael.'}]},'finishReason':'STOP'}]}]
    def handle(request):
        body=json.loads(request.content)
        if provider=='openai':assert body['store'] is False and body['stream'] is True
        return httpx.Response(200,text=''.join('data: '+json.dumps(row)+'\n\n' for row in records))
    original=httpx.AsyncClient
    monkeypatch.setattr('backend.intelligence.httpx.AsyncClient',lambda **kwargs:original(transport=httpx.MockTransport(handle),**kwargs))
    async def run():return ''.join([chunk async for chunk in generate_stream(config(tmp_path,provider),'Kurz antworten',{'question':'Hallo'})])
    assert asyncio.run(run())=='Hallo Michael.'
    records.pop()
    with pytest.raises(RuntimeError):asyncio.run(run())


def test_payload_compaction_is_bounded_and_preserves_source_diversity():
    rows=[{'source':source,'body':'Intro '*1000+'\nArbeitsbeginn am 02.11.2026 um 08:00 Uhr.\n'+'Ende '*1000} for source in ['paperless','imap','ntfy','calendar','contacts','memory']]
    payload={'question':'Wann ist mein Arbeitsbeginn?','sources':rows}
    result=compact_payload(payload)
    assert {row['source'] for row in result['sources']}=={row['source'] for row in rows}
    assert all(len(row['body'])<=2600 and '02.11.2026' in row['body'] for row in result['sources'])
    assert len(rows[0]['body'])>2600


def test_chat_sse_sends_progress_draft_and_one_saved_final_answer(tmp_path,monkeypatch):
    async def stream(*args,**kwargs):
        yield 'Photo';await asyncio.sleep(.15);yield 'synthese ist ein Prozess.'
    monkeypatch.setattr('backend.intelligence.generate_stream',stream)
    app=create_app(config(tmp_path))
    with TestClient(app) as client:
        response=client.post('/api/chat/stream',headers={'Authorization':'Bearer test'},json={'message':'Was ist Photosynthese?','share_external':True,'request_id':'a'*32})
        assert response.status_code==200 and response.headers['x-accel-buffering']=='no'
        assert 'event: progress' in response.text and 'Photo' in response.text
        final=[json.loads(block.split('data: ',1)[1]) for block in response.text.split('\n\n') if block.startswith('event: result')][0]
        assert final['answer']=='Photosynthese ist ein Prozess.' and final['first_token_seconds']>=0
        state=client.get('/api/chat/result/'+'a'*32,headers={'Authorization':'Bearer test'}).json()
        assert state['result']['answer']==final['answer']
        assert len(app.state.store.conversation(final['conversation_id'],None)['messages'])==2


def test_stream_draft_is_owner_scoped_and_cancel_does_not_save_partial_text(tmp_path,monkeypatch):
    first=asyncio.Event()
    async def stream(*args,**kwargs):
        yield 'Unfertiger Text';first.set();await asyncio.Event().wait()
    monkeypatch.setattr('backend.intelligence.generate_stream',stream)
    app=create_app(config(tmp_path))
    monkeypatch.setattr(app.state.accounts,'session',lambda token:{'id':token,'role':'member','email':'test@example.invalid'} if token else None)
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            headers={'Cookie':'butler_session=a','Origin':'http://127.0.0.1'}
            pending=asyncio.create_task(client.post('/api/chat/stream',headers=headers,json={'message':'Was ist Photosynthese?','share_external':True,'request_id':'b'*32}))
            await asyncio.wait_for(first.wait(),2)
            own=(await client.get('/api/chat/result/'+'b'*32,headers=headers)).json()
            assert own['draft_text']=='Unfertiger Text'
            assert (await client.get('/api/chat/result/'+'b'*32,headers={**headers,'Cookie':'butler_session=c'})).status_code==404
            await client.post('/api/chat/cancel',headers=headers,json={'request_id':'b'*32})
            response=await asyncio.wait_for(pending,2)
            assert response.status_code==200
            state=(await client.get('/api/chat/result/'+'b'*32,headers=headers)).json()
            assert state['status']=='failed' and state['result']['answer']!='Unfertiger Text'
    asyncio.run(scenario())


def test_index_first_skips_planning_and_live_calls_but_explicit_research_does_not(tmp_path,monkeypatch):
    app=create_app(config(tmp_path));calls=[]
    app.state.store.commit_item({'id':'repair','version':'1','title':'Fahrrad repariert','body':'Mein Fahrrad wurde am 03.10.2026 repariert.','source':'paperless'})
    async def plan(*args,**kwargs):calls.append('plan');return None
    async def live(*args,**kwargs):calls.append('live')
    async def answer(settings,task,payload):return 'Am 03.10.2026.'
    monkeypatch.setattr('backend.main.plan_question',plan);monkeypatch.setattr('backend.main.generate',answer)
    monkeypatch.setattr(app.state.worker,'search_mail_context',live);monkeypatch.setattr(app.state.worker,'search_document_context',live)
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'};body={'message':'Wann wurde mein Fahrrad repariert?','share_external':True}
        fast=client.post('/api/chat',headers=headers,json=body).json()
        assert fast['retrieval_mode']=='indexed' and calls==[]
        deep=client.post('/api/chat',headers=headers,json={**body,'research':True}).json()
        assert deep['retrieval_mode']=='live_enriched' and 'live' in calls
