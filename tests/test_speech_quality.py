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
    assert calls['decode']['beam_size']==5
