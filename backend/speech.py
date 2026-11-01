"""Local-only speech transcription. Model must be installed explicitly beforehand."""
import io
from pathlib import Path
from threading import Lock

_models={}
_lock=Lock()

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
            model=WhisperModel(path,device='cpu',compute_type='int8',cpu_threads=1,local_files_only=True)
            _models[path]=model
        samples=decode_audio(io.BytesIO(audio),sampling_rate=16000)
        if len(samples)>16000*60:
            raise ValueError('Bitte höchstens 60 Sekunden aufnehmen.')
        segments,_=model.transcribe(samples,language='de',vad_filter=True,beam_size=3,condition_on_previous_text=False)
        return ' '.join(segment.text.strip() for segment in segments).strip()
