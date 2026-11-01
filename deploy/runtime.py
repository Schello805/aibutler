"""Reuse immutable Python environments across releases; refresh at least weekly."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
import uuid

MAX_AGE=7*86400


def fingerprint(release,speech):
    requirements=['requirements.txt']+(['requirements-speech.txt'] if speech else [])
    payload={'python':sys.version,'executable':str(Path(sys.executable).resolve()),'abi':sysconfig.get_config_var('SOABI'),'platform':platform.platform(),'speech':speech,
             'requirements':{name:(release/name).read_text() for name in requirements}}
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def find_runtime(root,key,now):
    candidates=[]
    for path in root.glob(key+'-*'):
        if path.is_symlink() or not path.is_dir():continue
        try:
            state=json.loads((path/'.complete.json').read_text())
            age=now-float(state['created'])
            if state['fingerprint']==key and 0<=age<MAX_AGE and (path/'bin/python').is_file():
                candidates.append((state['created'],path))
        except (OSError,ValueError,KeyError,TypeError):continue
    return max(candidates,key=lambda entry:entry[0])[1] if candidates else None


def build_runtime(path,release,speech,cache):
    def run(arguments):subprocess.run(arguments,check=True,stdout=sys.stderr)
    run([sys.executable,'-m','venv',str(path)])
    python=str(path/'bin/python')
    requirements=['requirements.txt']+(['requirements-speech.txt'] if speech else [])
    command=[python,'-m','pip','install','--disable-pip-version-check','--cache-dir',str(cache)]
    for name in requirements:command.extend(['-r',str(release/name)])
    run(command)
    run([python,'-m','pip','check'])


def prepare_runtime(root,release,speech,refresh=False,builder=build_runtime,now=None):
    now=time.time() if now is None else now
    root.mkdir(mode=0o755,parents=True,exist_ok=True);root.chmod(0o755)
    key=fingerprint(release,speech)
    existing=None if refresh else find_runtime(root,key,now)
    if existing:
        print('[Butler] Abhängigkeiten unverändert · Python-Umgebung wiederverwenden.',file=sys.stderr)
        return existing
    cache=root.parent/'cache/pip';cache.mkdir(mode=0o700,parents=True,exist_ok=True)
    path=root/(key+'-'+uuid.uuid4().hex[:12])
    path.mkdir(mode=0o755)
    print('[Butler] Python-Umgebung erstellen · Paketdownloads werden zwischengespeichert.',file=sys.stderr)
    try:
        builder(path,release,speech,cache)
        # All contents are public package code; the service user needs read/execute access.
        for directory,dirs,files in os.walk(path):
            Path(directory).chmod(0o755)
            for name in files:
                file=Path(directory)/name
                if not file.is_symlink():file.chmod(0o755 if file.stat().st_mode & 0o111 else 0o644)
        marker=path/'.complete.json'
        marker.write_text(json.dumps({'fingerprint':key,'created':now})+'\n');marker.chmod(0o644)
        return path
    except BaseException:
        shutil.rmtree(path)
        raise


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',required=True,type=Path)
    parser.add_argument('--release',required=True,type=Path)
    parser.add_argument('--speech',required=True,choices=('true','false'))
    parser.add_argument('--refresh',action='store_true')
    args=parser.parse_args()
    print(prepare_runtime(args.root,args.release,args.speech=='true',args.refresh))
