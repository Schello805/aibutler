"""Root-only management helpers. Never print API keys or source contents."""
import json
import os
import shutil
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path('/opt/aibutler')
DATA = Path('/var/lib/aibutler')
CONFIG = Path('/etc/aibutler')
BACKUPS = Path('/var/backups/aibutler')
UNIT = Path('/etc/systemd/system/aibutler.service')


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def release_path(path):
    path = Path(path).resolve()
    if path.parent != (ROOT / 'releases').resolve() or not (path / 'backend/main.py').is_file():
        raise ValueError('Kein gültiges verwaltetes Release-Verzeichnis')
    return path


def archive_filter(member):
    parts = Path(member.name).parts
    if len(parts) >= 2 and parts[1] in ('models', 'cache'):
        return None  # Public model weights/cache are downloadable; not private state.
    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
        raise ValueError('Unerwarteter Link/Spezialdatei im privaten Datenverzeichnis')
    return member


def backup():
    release = release_path(ROOT / 'current')
    BACKUPS.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(BACKUPS, 0o700)
    target = Path(tempfile.mkdtemp(prefix=stamp() + '-', dir=BACKUPS))
    try:
        with tarfile.open(target / 'data.tar.gz', 'w:gz') as archive:
            archive.add(DATA, arcname='data', filter=archive_filter)
        for path in (CONFIG / 'install.json', CONFIG / 'service.env', UNIT):
            if path.is_file():
                shutil.copy2(path, target / path.name)
        (target / 'metadata.json').write_text(json.dumps({'release': str(release), 'created': stamp()}, indent=2))
        (target / 'COMPLETE').write_text('1\n')
    except BaseException:
        (target / 'INCOMPLETE').touch()
        raise
    return target


def extract_private_data(archive_path, destination):
    """Reject traversal, links and special files before extracting any member."""
    with tarfile.open(archive_path, 'r:gz') as archive:
        members = archive.getmembers()
        for member in members:
            parts = Path(member.name).parts
            if not parts or parts[0] != 'data' or '..' in parts or Path(member.name).is_absolute():
                raise ValueError('Unsicherer Pfad im Backup')
            if not (member.isfile() or member.isdir()) or member.issym() or member.islnk():
                raise ValueError('Links/Spezialdateien im Backup sind nicht erlaubt')
        for member in members:
            # Only simple regular files/directories validated above; no link dereferencing.
            relative = Path(*Path(member.name).parts[1:])
            path = destination / relative
            if member.isdir():
                path.mkdir(mode=0o700, parents=True, exist_ok=True)
            else:
                path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with archive.extractfile(member) as source, path.open('wb') as output:
                    shutil.copyfileobj(source, output)
                path.chmod(0o600)


def restore(target):
    import pwd
    target = Path(target).resolve()
    if not (target / 'COMPLETE').is_file():
        raise ValueError('Backup ist unvollständig oder nicht vorhanden')
    metadata = json.loads((target / 'metadata.json').read_text())
    release = release_path(metadata['release'])
    # Validate configuration before changing any persistent state.
    saved_config = json.loads((target / 'install.json').read_text())
    if not isinstance(saved_config.get('port'), int) or not 1 <= saved_config['port'] <= 65535:
        raise ValueError('Ungültige Installationskonfiguration im Backup')
    for name in ('service.env', UNIT.name):
        if not (target / name).is_file():
            raise ValueError('Dienstkonfiguration fehlt im Backup')
    user = pwd.getpwnam('aibutler')
    temporary = Path(tempfile.mkdtemp(prefix='.aibutler-restore-', dir=DATA.parent))
    try:
        extract_private_data(target / 'data.tar.gz', temporary)
        if not (temporary / 'config/.env').is_file():
            raise ValueError('Zugangsdaten-Datei fehlt im Backup')
        displaced = DATA.with_name('aibutler-before-restore-' + stamp())
        if DATA.exists():
            DATA.rename(displaced)  # Preserve newer state for manual recovery, never delete it.
            displaced.chmod(0o700)
            for name in ('models', 'cache'):
                old = displaced / name
                if old.exists() and not (temporary / name).exists():
                    old.rename(temporary / name)
        temporary.rename(DATA)
        for directory, dirs, files in os.walk(DATA):
            os.chown(directory, user.pw_uid, user.pw_gid)
            os.chmod(directory, 0o700)
            for name in files:
                path = Path(directory) / name
                if path.is_symlink():
                    # Public model caches may contain links; do not follow them as root.
                    os.lchown(path, user.pw_uid, user.pw_gid)
                else:
                    os.chown(path, user.pw_uid, user.pw_gid)
                    os.chmod(path, 0o600)
        for path in (CONFIG / 'install.json', CONFIG / 'service.env', UNIT):
            shutil.copy2(target / path.name, path)
            os.chmod(path, 0o644 if path == UNIT else 0o600)
        next_link = ROOT / '.restore-next'
        next_link.unlink(missing_ok=True)
        next_link.symlink_to(release)
        next_link.replace(ROOT / 'current')
        print('Wiederhergestellt:', target)
        print('Stand vor Wiederherstellung bleibt erhalten:', displaced)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def pair():
    from dotenv import dotenv_values
    config = json.loads((CONFIG / 'install.json').read_text())
    if not config.get('domain'):
        raise ValueError('Zuerst HTTPS konfigurieren: aibutler update --domain HOST --proxy-ip IP')
    token = dotenv_values(DATA / 'config/.env').get('API_TOKEN')
    if not token:
        raise ValueError('Interner Sitzungsschlüssel fehlt')
    port = int(config['port'])
    if not 1 <= port <= 65535:raise ValueError('Ungültiger Backend-Port')
    base = f"http://127.0.0.1:{port}"
    request = Request(base + '/api/session/pairing', method='POST', data=b'', headers={'Authorization': 'Bearer ' + token, 'Origin': base})
    try:
        with urlopen(request, timeout=15) as response:
            result = json.load(response)
    except (HTTPError, URLError):
        raise ValueError('Kopplung nicht verfügbar. Dienststatus und HTTPS-Konfiguration prüfen.') from None
    print('Auf dem Handy öffnen:', result['url'])
    print('Einmaliger Kopplungscode:', result['code'])
    print('10 Minuten gültig. Danach bleibt die Sitzung 30 Tage gespeichert.')


def main():
    if os.geteuid() != 0:
        raise SystemExit('Als root ausführen (sudo aibutler …).')
    os.umask(0o077)
    if len(sys.argv) < 2:
        raise SystemExit('Aktion fehlt')
    command = sys.argv[1]
    if command == 'backup':
        print(backup())
    elif command == 'restore' and len(sys.argv) == 3:
        restore(sys.argv[2])
    elif command == 'pair':
        pair()
    elif command == 'port':
        print(json.loads((CONFIG / 'install.json').read_text())['port'])
    else:
        raise SystemExit('Unbekannte Aktion')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit('Butler: ' + str(exc)) from None
