"""Download model weights only; never uploads recordings or personal data."""
from pathlib import Path
from faster_whisper.utils import download_model
path=Path(__file__).resolve().parent.parent/'data/models/whisper-base'
path.mkdir(parents=True,exist_ok=True)
download_model('base',output_dir=str(path))
print('Lokales Sprachmodell bereit:',path)
