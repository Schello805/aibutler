"""Local-only speech transcription. Model must be installed explicitly beforehand."""
import io
import os
from pathlib import Path
from threading import Lock

_models={}
_lock=Lock()

def speech_model_path(data_dir:Path):
    small=data_dir/'models/whisper-small'
    return small if (small/'model.bin').is_file() else data_dir/'models/whisper-base'

def transcribe_audio(audio:bytes,model_path:Path):
    if not (model_path/'model.bin').is_file():
        raise RuntimeError('Lokales Sprachmodell fehlt. Einrichtung in README ausführen.')
    import onnxruntime
    onnxruntime.disable_telemetry_events()
    from faster_whisper import WhisperModel
    from faster_whisper.audio import decode_audio
    with _lock:
        path=str(model_path.resolve())
        model=_models.get(path)
        if model is None:
            model=WhisperModel(path,device='cpu',compute_type='int8',cpu_threads=max(1,min(4,os.cpu_count() or 1)),local_files_only=True)
            _models[path]=model
        samples=decode_audio(io.BytesIO(audio),sampling_rate=16000)
        if len(samples)>16000*60:
            raise ValueError('Bitte höchstens 60 Sekunden aufnehmen.')
        segments,_=model.transcribe(samples,language='de',vad_filter=True,vad_parameters={'threshold':0.35,'min_silence_duration_ms':1500,'speech_pad_ms':500},beam_size=5,condition_on_previous_text=False)
        return ' '.join(segment.text.strip() for segment in segments).strip()
