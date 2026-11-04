"""Local transcription with preloaded weights, bounded CPU use and explicit decode profiles."""
import io
import os
from pathlib import Path
from threading import Lock
from time import perf_counter

_models={}
_lock=Lock()
_model_lock=Lock()

class SpeechBusy(RuntimeError):pass

def is_recognizing():return _lock.locked()

def speech_model_path(data_dir:Path):
    small=data_dir/'models/whisper-small'
    return small if (small/'model.bin').is_file() else data_dir/'models/whisper-base'

def cpu_threads(cgroup_root=Path('/sys/fs/cgroup')):
    limits=[4,os.cpu_count() or 1]
    try:limits.append(len(os.sched_getaffinity(0)))
    except (AttributeError,OSError):pass
    try:
        quota,period=(cgroup_root/'cpu.max').read_text().split()
        if quota!='max':limits.append(max(1,int(quota)//int(period)))
    except (OSError,ValueError,ZeroDivisionError):
        try:
            quota=int((cgroup_root/'cpu/cpu.cfs_quota_us').read_text());period=int((cgroup_root/'cpu/cpu.cfs_period_us').read_text())
            if quota>0:limits.append(max(1,quota//period))
        except (OSError,ValueError,ZeroDivisionError):pass
    return max(1,min(limits))

def load_model(model_path):
    if not (model_path/'model.bin').is_file():raise RuntimeError('Lokales Sprachmodell fehlt. Einrichtung in README ausführen.')
    path=str(model_path.resolve())
    with _model_lock:
        if path not in _models:
            import onnxruntime
            onnxruntime.disable_telemetry_events()
            from faster_whisper import WhisperModel
            _models[path]=WhisperModel(path,device='cpu',compute_type='int8',cpu_threads=cpu_threads(),local_files_only=True)
        return _models[path]

def warm_model(data_dir):
    path=speech_model_path(data_dir)
    if not (path/'model.bin').is_file():return False
    load_model(path)
    return True

def transcribe_audio(audio:bytes,model_path:Path,*,mode='fast',metrics=None):
    if mode not in ('fast','balanced','accurate'):raise ValueError('Unbekannter Erkennungsmodus')
    # A cancelled HTTP request cannot cancel a running native decoder. Do not queue
    # another native recognition behind it after the async route lock was released.
    if not _lock.acquire(blocking=False):raise SpeechBusy('Spracherkennung ist gerade beschäftigt')
    started=perf_counter();timings={}
    try:
        begin=perf_counter();model=load_model(model_path);timings['model_load_seconds']=round(perf_counter()-begin,3)
        from faster_whisper.audio import decode_audio
        begin=perf_counter();samples=decode_audio(io.BytesIO(audio),sampling_rate=16000);timings['audio_decode_seconds']=round(perf_counter()-begin,3)
        if len(samples)>16000*60:raise ValueError('Bitte höchstens 60 Sekunden aufnehmen.')
        begin=perf_counter()
        # One deterministic pass: no repeated temperature fallback on unclear audio.
        options={'beam_size':{'fast':1,'balanced':3,'accurate':5}[mode],'best_of':1,'temperature':0.0}
        segments,_=model.transcribe(samples,language='de',vad_filter=True,vad_parameters={'threshold':0.30,'min_speech_duration_ms':100,'min_silence_duration_ms':500,'speech_pad_ms':500},condition_on_previous_text=False,without_timestamps=True,**options)
        rows=list(segments)
        text=' '.join(segment.text.strip() for segment in rows).strip()
        # These are uncertainty signals, not calibrated confidence percentages.
        needs_review=any(getattr(row,'avg_logprob',0)<-1.0 or getattr(row,'compression_ratio',0)>2.4 or getattr(row,'no_speech_prob',0)>.6 for row in rows if row.text.strip())
        timings.update(recognition_seconds=round(perf_counter()-begin,3),total_seconds=round(perf_counter()-started,3),audio_seconds=round(len(samples)/16000,2),mode=mode,needs_review=needs_review,cpu_threads=cpu_threads(),model=model_path.name)
        if metrics is not None:metrics.update(timings)
        return text
    finally:_lock.release()
