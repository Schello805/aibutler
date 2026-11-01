"""Installer validation and real backup/restore round-trips, without modifying the host."""
import io
import json
import os
import sqlite3
import subprocess
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from backend.config import Settings
from backend.main import create_app
from deploy import maintenance

PROJECT = Path(__file__).resolve().parents[1]


def installer(*args):
    return subprocess.run(['bash', str(PROJECT/'install.sh'), '--check', *args], capture_output=True, text=True)


def test_installer_default_is_loopback_and_explicit_https_requires_proxy():
    result = installer('--local-only')
    assert result.returncode == 0
    assert 'bind=127.0.0.1' in result.stdout
    result = installer('--domain', 'ai.example.com', '--proxy-ip', '192.168.1.10', '--no-speech', '--port', '8012')
    assert result.returncode == 0
    assert 'domain=https://ai.example.com' in result.stdout
    assert 'bind=0.0.0.0' in result.stdout and 'speech=false' in result.stdout and 'port=8012' in result.stdout


@pytest.mark.parametrize('args', [
    ['--domain', 'ai.example.com'], ['--proxy-ip', '192.168.1.10'],
    ['--domain', 'http://ai.example.com', '--proxy-ip', '192.168.1.10'],
    ['--domain', 'https://ai.example.com:0', '--proxy-ip', '192.168.1.10'],
    ['--domain', 'https://user:password@ai.example.com', '--proxy-ip', '192.168.1.10'],
    ['--domain', 'https://ai.example.com/path', '--proxy-ip', '192.168.1.10'],
    ['--domain', 'ai.example.com', '--proxy-ip', '*'],
    ['--domain', 'ai.example.com', '--proxy-ip', '0.0.0.0'],
    ['--port', '0'], ['--port', '70000'], ['--port', 'hello'],
    ['--ref', '--upload-pack=bad'], ['--ref', '../../outside'], ['--unknown'], ['--domain'],
])
def test_installer_rejects_invalid_flags_without_installing(args):
    assert installer(*args).returncode != 0


def test_managed_env_file_is_preserved_when_saving_connections(tmp_path, monkeypatch):
    shared = tmp_path/'private/config/.env'
    shared.parent.mkdir(parents=True)
    shared.write_text("IMAP_PASSWORD='preserve-example-secret'\n")
    monkeypatch.setenv('BUTLER_ENV_FILE', str(shared))
    settings = Settings(_env_file=shared, data_dir=tmp_path/'data', api_token='test', worker_enabled=False)
    with TestClient(create_app(settings)) as client:
        result=client.put('/api/connections/imap', headers={'Authorization':'Bearer test'}, json={'imap_host':'imap.example.com'})
        assert result.status_code == 200
    assert 'preserve-example-secret' in shared.read_text()
    assert 'imap.example.com' in shared.read_text()
    assert shared.stat().st_mode & 0o777 == 0o600


def test_health_and_disabled_speech(tmp_path):
    app=create_app(Settings(_env_file=None, data_dir=tmp_path, api_token='test', worker_enabled=False, speech_enabled=False))
    with TestClient(app) as client:
        assert client.get('/healthz').json()=={'status':'ok'}
        assert client.get('/api/status').status_code==401
        assert client.post('/api/speech/transcribe', headers={'Authorization':'Bearer test','Content-Type':'application/octet-stream'}, content=b'audio').status_code==503


def setup_managed_tree(tmp_path, monkeypatch):
    root=tmp_path/'opt/aibutler';data=tmp_path/'var/lib/aibutler';config=tmp_path/'etc/aibutler'
    backups=tmp_path/'var/backups/aibutler';unit=tmp_path/'etc/systemd/aibutler.service'
    release=root/'releases/old';(release/'backend').mkdir(parents=True)
    (release/'backend/main.py').touch()
    (root/'current').symlink_to(release)
    (data/'config').mkdir(parents=True);config.mkdir(parents=True);unit.parent.mkdir(parents=True)
    (data/'config/.env').write_text('API_TOKEN=example-private-key\n')
    (config/'install.json').write_text(json.dumps({'port':8010,'domain':'https://ai.example.com'}))
    (config/'service.env').write_text('BUTLER_PORT=8010\n');unit.write_text('old service')
    for name,value in [('ROOT',root),('DATA',data),('CONFIG',config),('BACKUPS',backups),('UNIT',unit)]:monkeypatch.setattr(maintenance,name,value)
    return root,data,config,release


def test_backup_restore_recovers_database_credentials_and_code_without_losing_newer_data(tmp_path, monkeypatch):
    import pwd
    monkeypatch.setattr(pwd,'getpwnam',lambda _:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    root,data,config,old=setup_managed_tree(tmp_path,monkeypatch)
    with sqlite3.connect(data/'butler.sqlite3') as db:
        db.execute('CREATE TABLE messages(text TEXT)');db.execute('INSERT INTO messages VALUES(?)',('old private conversation',))
    (data/'models').mkdir();(data/'models/model.bin').write_bytes(b'redownloadable model')
    (data/'cache').mkdir();(data/'cache/download').write_bytes(b'public cache')
    target=maintenance.backup()
    assert target.stat().st_mode & 0o777 == 0o700
    with tarfile.open(target/'data.tar.gz') as archive:
        names=archive.getnames()
        assert 'data/config/.env' in names and 'data/butler.sqlite3' in names
        assert not any(name.startswith(('data/models','data/cache')) for name in names)
    new=root/'releases/new';(new/'backend').mkdir(parents=True);(new/'backend/main.py').touch()
    (root/'current').unlink();(root/'current').symlink_to(new)
    (data/'config/.env').write_text('API_TOKEN=newer-private-key\n')
    with sqlite3.connect(data/'butler.sqlite3') as db:db.execute('INSERT INTO messages VALUES(?)',('newer conversation',))
    (config/'install.json').write_text(json.dumps({'port':8012}))
    maintenance.restore(target)
    assert (root/'current').resolve()==old
    assert 'example-private-key' in (data/'config/.env').read_text()
    assert json.loads((config/'install.json').read_text())['port']==8010
    with sqlite3.connect(data/'butler.sqlite3') as db:assert db.execute('SELECT text FROM messages').fetchall()==[('old private conversation',)]
    assert (data/'models/model.bin').read_bytes()==b'redownloadable model'
    displaced=list(data.parent.glob('aibutler-before-restore-*'))
    assert len(displaced)==1
    assert 'newer-private-key' in (displaced[0]/'config/.env').read_text()
    with sqlite3.connect(displaced[0]/'butler.sqlite3') as db:assert db.execute('SELECT count(*) FROM messages').fetchone()[0]==2


@pytest.mark.parametrize('name,link', [('data/../../outside',False),('/absolute',False),('data/link',True)])
def test_restore_rejects_unsafe_archive_before_extracting_anything(tmp_path,name,link):
    archive=tmp_path/'bad.tar.gz';destination=tmp_path/'destination';destination.mkdir()
    with tarfile.open(archive,'w:gz') as output:
        good=tarfile.TarInfo('data/good');good.size=1;output.addfile(good,io.BytesIO(b'x'))
        bad=tarfile.TarInfo(name)
        if link:bad.type=tarfile.SYMTYPE;bad.linkname='/etc/passwd'
        output.addfile(bad)
    with pytest.raises(ValueError):maintenance.extract_private_data(archive,destination)
    assert list(destination.iterdir())==[]


def test_cli_account_creation_never_prints_password(tmp_path,monkeypatch,capsys):
    import getpass
    from contextlib import nullcontext
    from backend.accounts import Accounts
    from backend.store import Store
    _,data,_,_=setup_managed_tree(tmp_path,monkeypatch)
    monkeypatch.setattr(getpass,'getpass',lambda *args:'example-private-passphrase')
    monkeypatch.setattr(maintenance,'service_identity',nullcontext)
    maintenance.user_command(['add','owner@example.org'])
    text=capsys.readouterr().out
    assert 'example-private-passphrase' not in text and 'example-private-key' not in text
    accounts=Accounts(Store(data))
    assert accounts.list()[0]['role']=='admin'
    assert accounts.authenticate('owner@example.org','example-private-passphrase','cli')

def test_restore_does_not_resurrect_previously_revoked_sessions(tmp_path,monkeypatch):
    import pwd
    from backend.accounts import Accounts
    from backend.store import Store
    monkeypatch.setattr(pwd,'getpwnam',lambda _:SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid()))
    _,data,_,_=setup_managed_tree(tmp_path,monkeypatch)
    accounts=Accounts(Store(data))
    user=accounts.create('owner@example.org','a-long-example-passphrase')
    token,_=accounts.new_session(user['id'])
    target=maintenance.backup()
    accounts.revoke(token)
    maintenance.restore(target)
    restored=Accounts(Store(data))
    assert restored.session(token) is None
    assert restored.authenticate(user['email'],'a-long-example-passphrase','test')


def test_installer_accepts_forced_dependency_refresh():
    assert installer('--refresh-deps').returncode==0
