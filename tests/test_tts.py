"""Private local synthesis API; no model downloads, recordings or provider calls."""
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from backend import tts


def test_synthesis_requires_auth_and_limits_input(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    calls=[]
    monkeypatch.setattr(tts,'synthesize',lambda text,path:calls.append((text,path)) or b'RIFF-test-wave')
    with TestClient(app) as client:
        assert client.post('/api/speech/synthesize',json={'text':'Hallo'}).status_code==401
        headers={'Authorization':'Bearer test'}
        for text in ('','x'*2001):assert client.post('/api/speech/synthesize',headers=headers,json={'text':text}).status_code==422
        result=client.post('/api/speech/synthesize',headers=headers,json={'text':'Hallo Michael.'})
        assert result.status_code==200 and result.content==b'RIFF-test-wave'
        assert result.headers['content-type']=='audio/wav'
        assert result.headers['cache-control']=='no-store'
        assert calls==[('Hallo Michael.',tmp_path/'models/piper')]


def test_missing_voice_never_downloads_and_busy_is_reported(tmp_path,monkeypatch):
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test'}
        assert client.post('/api/speech/synthesize',headers=headers,json={'text':'Hallo'}).status_code==503
        def busy(*args):raise tts.VoiceBusy()
        monkeypatch.setattr(tts,'synthesize',busy)
        assert client.post('/api/speech/synthesize',headers=headers,json={'text':'Hallo'}).status_code==429
