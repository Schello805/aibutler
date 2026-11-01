"""Download model weights only; never uploads recordings or personal data."""
import os
from pathlib import Path
from faster_whisper.utils import download_model
root=Path(os.environ.get('DATA_DIR',str(Path(__file__).resolve().parent.parent/'data')))
path=root/'models/whisper-small'
path.mkdir(parents=True,exist_ok=True)
download_model('small',output_dir=str(path))
print('Lokales Sprachmodell bereit:',path)
