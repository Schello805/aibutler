"""Download public embedding weights only; no personal documents are read."""
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.semantic import setup_model
root=Path(os.environ.get('DATA_DIR',str(Path(__file__).resolve().parent.parent/'data')))/'models/semantic'
setup_model(root)
print('Lokales semantisches Suchmodell bereit:',root)
