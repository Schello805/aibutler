from backend.speech import speech_model_path

def test_prefers_larger_local_model_but_keeps_existing_installations_working(tmp_path):
    assert speech_model_path(tmp_path)==tmp_path/'models/whisper-base'
    small=tmp_path/'models/whisper-small';small.mkdir(parents=True)
    assert speech_model_path(tmp_path)==tmp_path/'models/whisper-base'
    (small/'model.bin').touch()
    assert speech_model_path(tmp_path)==small

def test_transcription_keeps_all_segments_with_local_model(monkeypatch,tmp_path):
    import sys
    import types
    from backend import speech
    path=tmp_path/'model';path.mkdir();(path/'model.bin').touch()
    calls={}
    class Model:
        def __init__(self,*args,**kwargs):calls['model']=kwargs
        def transcribe(self,samples,**kwargs):
            calls['decode']=kwargs
            return iter([types.SimpleNamespace(text='Bitte im Arbeitsvertrag'),types.SimpleNamespace(text='von Sielaff nach Urlaubstagen schauen.')]),None
    monkeypatch.setitem(sys.modules,'onnxruntime',types.SimpleNamespace(disable_telemetry_events=lambda:None))
    monkeypatch.setitem(sys.modules,'faster_whisper',types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setitem(sys.modules,'faster_whisper.audio',types.SimpleNamespace(decode_audio=lambda *a,**k:[0]*16000))
    text=speech.transcribe_audio(b'fake audio',path)
    assert text=='Bitte im Arbeitsvertrag von Sielaff nach Urlaubstagen schauen.'
    assert calls['model']['local_files_only'] is True
    assert calls['decode']['vad_parameters']['speech_pad_ms']==500
    assert calls['decode']['beam_size']==1
    assert calls['decode']['best_of']==1 and calls['decode']['temperature']==0.0
    assert calls['decode']['vad_parameters']['min_speech_duration_ms']==100
    assert calls['decode']['vad_parameters']['threshold']==.30

def test_cpu_threads_respect_container_quota_and_affinity(tmp_path,monkeypatch):
    import os
    from backend.speech import cpu_threads
    monkeypatch.setattr(os,'cpu_count',lambda:64)
    monkeypatch.setattr(os,'sched_getaffinity',lambda pid:{0,1,2,3},raising=False)
    (tmp_path/'cpu.max').write_text('100000 100000')
    assert cpu_threads(tmp_path)==1
    (tmp_path/'cpu.max').write_text('200000 100000')
    assert cpu_threads(tmp_path)==2
    (tmp_path/'cpu.max').write_text('max 100000')
    assert cpu_threads(tmp_path)==4


def test_preload_and_profiles_reuse_one_local_model(monkeypatch,tmp_path):
    import sys
    import types
    from backend import speech
    root=tmp_path/'models/whisper-small';root.mkdir(parents=True);(root/'model.bin').touch()
    builds=[];options=[]
    class Model:
        def __init__(self,*args,**kwargs):builds.append(kwargs)
        def transcribe(self,samples,**kwargs):options.append(kwargs);return iter([types.SimpleNamespace(text='Rollo Wohnzimmer herunterfahren.')]),None
    monkeypatch.setitem(sys.modules,'onnxruntime',types.SimpleNamespace(disable_telemetry_events=lambda:None))
    monkeypatch.setitem(sys.modules,'faster_whisper',types.SimpleNamespace(WhisperModel=Model))
    monkeypatch.setitem(sys.modules,'faster_whisper.audio',types.SimpleNamespace(decode_audio=lambda *a,**k:[0]*16000))
    assert speech.warm_model(tmp_path)
    fast={};accurate={}
    assert speech.transcribe_audio(b'fake',root,metrics=fast)=='Rollo Wohnzimmer herunterfahren.'
    assert speech.transcribe_audio(b'fake',root,mode='accurate',metrics=accurate)=='Rollo Wohnzimmer herunterfahren.'
    assert len(builds)==1 and builds[0]['local_files_only']
    assert options[0]['beam_size']==1 and options[0]['temperature']==0.0
    assert options[1]['beam_size']==5 and options[1]['temperature']==0.0
    assert all(option['without_timestamps'] for option in options)
    assert fast['audio_seconds']==1 and fast['mode']=='fast' and accurate['mode']=='accurate'
    assert fast['needs_review'] is False and accurate['needs_review'] is False
    assert all(fast[key]>=0 for key in ('model_load_seconds','audio_decode_seconds','recognition_seconds','total_seconds'))


def test_native_recognition_does_not_queue_behind_cancelled_request(tmp_path):
    import pytest
    from backend import speech
    speech._lock.acquire()
    try:
        assert speech.is_recognizing()
        with pytest.raises(speech.SpeechBusy):speech.transcribe_audio(b'fake',tmp_path)
    finally:speech._lock.release()


def test_api_mode_and_busy_state_are_explicit_and_timings_contain_no_audio(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    from backend import speech
    calls=[]
    def transcribe(audio,path,*,mode,metrics):
        calls.append(mode);metrics.update(recognition_seconds=.3,model_load_seconds=0,audio_decode_seconds=.01)
        return 'Erfundener Beispielsatz.'
    monkeypatch.setattr(speech,'transcribe_audio',transcribe)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    with TestClient(app) as client:
        headers={'Authorization':'Bearer test','Content-Type':'application/octet-stream','X-Speech-Mode':'accurate'}
        result=client.post('/api/speech/transcribe',headers=headers,content=b'private-audio-bytes')
        assert result.status_code==200 and result.json()['mode']=='accurate'
        assert result.json()['timings']['recognition_seconds']==.3 and 'private-audio' not in result.text
        invalid=client.post('/api/speech/transcribe',headers={**headers,'X-Speech-Mode':'unknown'},content=b'fake')
        assert invalid.status_code==422 and calls==['accurate']
        def busy(*args,**kwargs):raise speech.SpeechBusy('private internal detail')
        monkeypatch.setattr(speech,'transcribe_audio',busy)
        refused=client.post('/api/speech/transcribe',headers=headers,content=b'fake')
        assert refused.status_code==429 and 'private internal detail' not in refused.text

def test_startup_preloads_speech_without_blocking_health_endpoint(tmp_path,monkeypatch):
    import threading
    from fastapi.testclient import TestClient
    from backend import speech
    from backend.config import Settings
    from backend.main import create_app
    entered=threading.Event();release=threading.Event()
    def warm(root):entered.set();release.wait(2)
    monkeypatch.setattr(speech,'warm_model',warm)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    with TestClient(app) as client:
        try:
            assert entered.wait(1)
            assert client.get('/healthz').status_code==200 and not release.is_set()
        finally:release.set()


def test_background_indexing_yields_to_recognition(tmp_path,monkeypatch):
    import asyncio
    from backend import speech
    from backend.worker import Worker
    from backend.store import Store
    from backend.config import Settings
    busy=[True];indexed=[]
    monkeypatch.setattr(speech,'is_recognizing',lambda:busy[0])
    worker=Worker(Settings(_env_file=None,data_dir=tmp_path),Store(tmp_path))
    monkeypatch.setattr(worker.semantic,'index_batch',lambda:indexed.append(True) or 0)
    async def scenario():
        task=asyncio.create_task(worker.semantic_loop())
        try:
            await asyncio.sleep(.02);assert not indexed
            busy[0]=False
            await asyncio.sleep(.15);assert indexed
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
    asyncio.run(scenario())


def test_new_synthesis_does_not_compete_with_active_recognition(tmp_path,monkeypatch):
    import pytest
    from backend import speech,tts
    monkeypatch.setattr(speech,'is_recognizing',lambda:True)
    with pytest.raises(tts.VoiceBusy):tts.synthesize('Öffentliches Testbeispiel.',tmp_path)

def test_overlapping_uploads_do_not_queue_a_second_recognition(tmp_path,monkeypatch):
    import asyncio
    import threading
    import httpx
    from backend import speech
    from backend.config import Settings
    from backend.main import create_app
    running=threading.Event();release=threading.Event();calls=[]
    def transcribe(audio,path,**kwargs):calls.append(audio);running.set();release.wait(3);return 'Testtext.'
    monkeypatch.setattr(speech,'transcribe_audio',transcribe)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    async def scenario():
        uploaded=asyncio.Event();finish_upload=asyncio.Event()
        async def slow_body():
            uploaded.set();await finish_upload.wait();yield b'second'
        headers={'Authorization':'Bearer test','Content-Type':'application/octet-stream'}
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
            second=asyncio.create_task(client.post('/api/speech/transcribe',headers=headers,content=slow_body()))
            await uploaded.wait()
            first=asyncio.create_task(client.post('/api/speech/transcribe',headers=headers,content=b'first'))
            try:
                assert await asyncio.to_thread(running.wait,1)
                finish_upload.set();response=await asyncio.wait_for(second,1)
                assert response.status_code==429 and calls==[b'first']
            finally:
                release.set();finish_upload.set();await asyncio.gather(first,second,return_exceptions=True)
    asyncio.run(scenario())


def test_balanced_recognition_keeps_whole_sentence_and_exposes_uncertainty(monkeypatch,tmp_path):
    import sys
    import types
    from backend import speech
    options=[]
    class Model:
        def transcribe(self,samples,**kwargs):
            options.append(kwargs)
            return iter([types.SimpleNamespace(text='Bitte prüfe',avg_logprob=-.2,compression_ratio=1.1,no_speech_prob=.01),types.SimpleNamespace(text='meinen Arbeitsvertrag.',avg_logprob=-1.3,compression_ratio=1.2,no_speech_prob=.1)]),None
    monkeypatch.setattr(speech,'load_model',lambda path:Model())
    monkeypatch.setitem(sys.modules,'faster_whisper.audio',types.SimpleNamespace(decode_audio=lambda *a,**k:[0]*16000))
    metrics={}
    assert speech.transcribe_audio(b'synthetic',tmp_path,mode='balanced',metrics=metrics)=='Bitte prüfe meinen Arbeitsvertrag.'
    assert metrics['needs_review'] is True
    assert options[0]['beam_size']==3 and options[0]['temperature']==0.0
    assert options[0]['without_timestamps'] is True


def test_balanced_api_default_passes_review_flag_without_audio_or_internal_scores(tmp_path,monkeypatch):
    from fastapi.testclient import TestClient
    from backend.config import Settings
    from backend.main import create_app
    from backend import speech
    def transcribe(audio,path,*,mode,metrics):
        assert mode=='balanced'
        metrics['needs_review']=True
        return 'Erfundene unsichere Frage.'
    monkeypatch.setattr(speech,'transcribe_audio',transcribe)
    app=create_app(Settings(_env_file=None,data_dir=tmp_path,api_token='test',worker_enabled=False))
    with TestClient(app) as client:
        result=client.post('/api/speech/transcribe',headers={'Authorization':'Bearer test','Content-Type':'application/octet-stream'},content=b'synthetic-audio')
        assert result.status_code==200 and result.json()['needs_review'] is True
        assert result.json()['mode']=='balanced' and 'synthetic-audio' not in result.text
