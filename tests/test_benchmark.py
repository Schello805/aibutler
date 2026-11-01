import time
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app


def test_synthetic_model_comparison_is_admin_only_and_does_not_change_config(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False,llm_api_key='synthetic-key',llm_model='original'))
    monkeypatch.setattr(app.state.accounts,'session',lambda token:{'id':token,'role':'admin' if token=='a' else 'member','email':'test@example.invalid'} if token else None)
    calls=[]
    async def stream(settings,task,payload):
        calls.append(payload)
        yield 'Test 12.12.2030 09:30 01.02.2031 08:00 Empfang'
    monkeypatch.setattr('backend.benchmark.generate_stream',stream)
    with TestClient(app,base_url='https://testserver') as client:
        headers={'Cookie':'butler_session=a','Origin':'https://testserver'}
        assert client.post('/api/benchmarks',headers={**headers,'Cookie':'butler_session=b'},json={'provider':'openai','models':['one']}).status_code==403
        response=client.post('/api/benchmarks',headers=headers,json={'provider':'openai','models':['one','two']})
        assert response.status_code==200
        identity=response.json()['id']
        for _ in range(50):
            result=client.get('/api/benchmarks/'+identity,headers=headers).json()
            if result['status']=='done':break
            time.sleep(.01)
        assert result['status']=='done' and len(result['results'])==6
        assert all(row['fact_check'] for row in result['results'])
        assert app.state.worker.s.llm_model=='original'
        assert len(calls)==6 and all('conversation' not in payload and 'context' not in payload for payload in calls)
        assert 'synthetic-key' not in str(result)
