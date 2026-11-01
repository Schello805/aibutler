"""Install public Piper voice weights only. No user content is accessed or uploaded."""
import os
from pathlib import Path
from huggingface_hub import hf_hub_download

os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
root=Path(os.environ.get('DATA_DIR',str(Path(__file__).resolve().parent.parent/'data')))/'models/piper'
root.mkdir(parents=True,exist_ok=True)
for filename in ('de_DE-thorsten-high.onnx','de_DE-thorsten-high.onnx.json','MODEL_CARD'):
    target=root/filename
    if target.is_file() and target.stat().st_size:continue
    downloaded=hf_hub_download('rhasspy/piper-voices',filename='de/de_DE/thorsten/high/'+filename,revision='v1.0.0',token=False)
    temporary=target.with_suffix(target.suffix+'.part')
    import shutil
    shutil.copyfile(downloaded,temporary);temporary.replace(target)
print('Lokale neuronale Stimme bereit:',root)
