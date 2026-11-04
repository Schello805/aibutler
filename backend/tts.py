"""Offline German neural synthesis. No runtime model downloads or text persistence."""
import io
import json
import re
import threading
import wave
from pathlib import Path

VOICE='de_DE-thorsten-high'
_lock=threading.Lock()
_voices={}

class VoiceBusy(RuntimeError):pass

def ready(root):
    root=Path(root)
    return (root/(VOICE+'.onnx')).is_file() and (root/(VOICE+'.onnx.json')).is_file()

def synthesize(text,root):
    from .speech import is_recognizing
    if is_recognizing():raise VoiceBusy('Spracherkennung hat gerade Vorrang.')
    if not ready(root):raise RuntimeError('Lokale Stimme fehlt. Bitte aibutler update mit Sprachunterstützung ausführen.')
    if not _lock.acquire(blocking=False):raise VoiceBusy('Sprachausgabe ist gerade beschäftigt.')
    try:
        import onnxruntime as ort
        from piper import PiperVoice
        from piper.config import PiperConfig,SynthesisConfig
        ort.disable_telemetry_events()
        path=Path(root)/ (VOICE+'.onnx')
        voice=_voices.get(str(path.resolve()))
        if voice is None:
            options=ort.SessionOptions();options.intra_op_num_threads=1;options.inter_op_num_threads=1
            voice=PiperVoice(config=PiperConfig.from_dict(json.loads(Path(str(path)+'.json').read_text())),session=ort.InferenceSession(str(path),sess_options=options,providers=['CPUExecutionProvider']))
            _voices[str(path.resolve())]=voice
        text=re.sub(r'\[([^\]]+)\]\([^)]*\)',r'\1',text)
        text=text.replace('**','').replace('`','').replace('kWh','Kilowattstunden').replace('€',' Euro ')
        output=io.BytesIO()
        # Natural voice defaults; avoid artificially speeding up speech.
        with wave.open(output,'wb') as wav:
            voice.synthesize_wav(text,wav,syn_config=SynthesisConfig(length_scale=1.0))
        return output.getvalue()
    finally:_lock.release()
