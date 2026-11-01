#!/usr/bin/env bash
# AI Butler installer for Debian/Ubuntu LXC/VM with systemd.
set -Eeuo pipefail
umask 077
export DEBIAN_FRONTEND=noninteractive
export GIT_TERMINAL_PROMPT=0
export PIP_NO_INPUT=1
REPOSITORY='https://github.com/Schello805/aibutler.git'
APP_ROOT=/opt/aibutler
APP_DATA=/var/lib/aibutler
APP_CONFIG=/etc/aibutler
MANIFEST=$APP_CONFIG/install.json
MODE=auto
REF=main
PORT=8010
DOMAIN=''
PROXY_IP=''
SPEECH=true
SEMANTIC=true
CHECK=false
REFRESH_DEPS=false
INSTALL_TEMP=''
OLD_RELEASE=''
BACKUP=''
STOPPED=false
CHANGED=false
SUCCESS=false

usage() {
    cat <<'HELP'
AI Butler – Installation und Update (Debian 12+/Ubuntu 24.04+, systemd)

  bash install.sh [--domain HOST] [--proxy-ip IP] [--port 8010]
                  [--no-speech] [--ref main]
  bash install.sh --update [dieselben Optionen]
  bash install.sh --check [Optionen]   Nur Argumente prüfen, nichts installieren

  --domain HOST       Eigene HTTPS-Adresse, z. B. ai.example.com
  --proxy-ip IP       Konkrete IP des HTTPS-Reverse-Proxys (kein *)
  --local-only        Nur Loopback; bestehende Domain/Proxy-IP entfernen
  --no-semantic       Ohne lokales semantisches Suchmodell (Volltext bleibt aktiv)
  --semantic          Lokale semantische Suche aktivieren (Standard)
  --no-speech         Ohne lokales Sprachmodell und Sprach-Abhängigkeiten
  --speech            Lokale Sprache aktivieren (Standard der Erstinstallation)
  --refresh-deps      Python-Pakete frisch auflösen (sonst Wiederverwendung bis 7 Tage)
  --ref REF           Git-Branch, Tag oder Commit; Standard main
  --port PORT         Backend-Port; Standard 8010
  --update            Bestehende verwaltete Installation aktualisieren
  --help              Hilfe

Ohne Domain/Proxy-IP bleibt der Dienst nur auf 127.0.0.1 erreichbar.
Zugriff zunächst per SSH-Tunnel, später HTTPS-Proxy konfigurieren.
Bei Updates werden nicht angegebene Optionen beibehalten.
HELP
}
log() { printf '\n[Butler] %s\n' "$*"; }
die() { printf '\n[Butler] Fehler: %s\n' "$*" >&2; exit 1; }
need_value() { [[ $# -ge 2 && -n $2 ]] || die "Wert für $1 fehlt"; }

# Help and validation do not depend on existing/root-only configuration.
for argument in "$@"; do
    case $argument in --help|-h) usage; exit 0 ;; --check) CHECK=true ;; esac
done
# Load only known JSON fields; never source a credentials file as shell code.
if [[ -f $MANIFEST ]] && ! $CHECK; then
    command -v python3 >/dev/null || die 'python3 fehlt zum Lesen der Installation'
    config_values=$(python3 - "$MANIFEST" <<'PY'
import json,sys
c=json.load(open(sys.argv[1]))
for key,default in [('ref','main'),('port',8010),('domain',''),('proxy_ip',''),('speech',True),('semantic',True)]:
    value=c.get(key,default)
    if isinstance(value,bool):value=str(value).lower()
    if '\n' in str(value) or '\r' in str(value):raise ValueError('Invalid install configuration')
    print(value)
PY
    )
    # Six known fields, including possibly empty domain/proxy values.
    { IFS= read -r REF; IFS= read -r PORT; IFS= read -r DOMAIN; IFS= read -r PROXY_IP; IFS= read -r SPEECH; IFS= read -r SEMANTIC; } <<< "$config_values"
fi
PREVIOUS_PORT=$PORT
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help|-h) usage; exit 0 ;;
        --update) MODE=update; shift ;;
        --check) CHECK=true; shift ;;
        --domain) need_value "$@"; DOMAIN=$2; shift 2 ;;
        --proxy-ip) need_value "$@"; PROXY_IP=$2; shift 2 ;;
        --port) need_value "$@"; PORT=$2; shift 2 ;;
        --ref) need_value "$@"; REF=$2; shift 2 ;;
        --no-semantic) SEMANTIC=false; shift ;;
        --semantic) SEMANTIC=true; shift ;;
        --no-speech) SPEECH=false; shift ;;
        --speech) SPEECH=true; shift ;;
        --refresh-deps) REFRESH_DEPS=true; shift ;;
        --local-only) DOMAIN=''; PROXY_IP=''; shift ;;
        *) die "Unbekannte Option: $1 (siehe --help)" ;;
    esac
done
if ! command -v python3 >/dev/null; then
    if ! $CHECK && [[ $(uname -s) == Linux && $EUID -eq 0 && -d /run/systemd/system ]] && command -v apt-get >/dev/null; then
        log 'Python für die Installation bereitstellen …'
        apt-get update
        apt-get install -y --no-install-recommends python3
    else
        die 'python3 wird benötigt (apt-get update && apt-get install -y python3)'
    fi
fi
# Validate before network, root operations or changes to an existing service.
DOMAIN=$(python3 - "$DOMAIN" "$PROXY_IP" "$PORT" "$REF" "$SPEECH" <<'PY'
import ipaddress,re,sys
from urllib.parse import urlsplit
origin,proxy,port,ref,speech=sys.argv[1:]
if not port.isascii() or not port.isdigit() or not 1<=int(port)<=65535:raise SystemExit('Port muss zwischen 1 und 65535 liegen')
if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._/-]{0,199}',ref) or '..' in ref:raise SystemExit('Ungültiger Git-Ref')
if speech not in ('true','false'):raise SystemExit('Ungültige Sprachkonfiguration')
if bool(origin)!=bool(proxy):raise SystemExit('--domain und --proxy-ip müssen zusammen angegeben werden')
if origin:
    if '://' not in origin:origin='https://'+origin
    p=urlsplit(origin)
    try:valid_port=p.port
    except ValueError:raise SystemExit('Ungültiger HTTPS-Port')
    if valid_port==0:raise SystemExit('Ungültiger HTTPS-Port')
    if p.scheme!='https' or not p.hostname or p.username or p.password or p.path not in ('','/') or p.query or p.fragment or not re.fullmatch(r'[A-Za-z0-9.-]+',p.hostname) or any(c.isspace() for c in origin):raise SystemExit('Domain muss eine reine HTTPS-Adresse ohne Pfad sein')
    if p.hostname in ('localhost','127.0.0.1'):raise SystemExit('Für den Proxy eine eigene Domain verwenden')
    try:
        address=ipaddress.ip_address(proxy)
        if address.is_unspecified or address.is_multicast:raise ValueError()
    except ValueError:raise SystemExit('Proxy-IP muss eine einzelne konkrete IPv4-/IPv6-Adresse sein')
    origin=origin.rstrip('/')
print(origin)
PY
) || die 'Installationsoptionen sind ungültig'
BIND=127.0.0.1
[[ -z $DOMAIN ]] || BIND=0.0.0.0
if $CHECK; then
    printf 'Konfiguration gültig: ref=%s port=%s bind=%s domain=%s proxy=%s speech=%s semantic=%s\n' "$REF" "$PORT" "$BIND" "${DOMAIN:-keine}" "${PROXY_IP:-keiner}" "$SPEECH" "$SEMANTIC"
    exit 0
fi
[[ $(uname -s) == Linux ]] || die 'Dieses Skript ist für Linux-LXC/VM, nicht für macOS. Lokale Entwicklung: README.'
[[ $EUID -eq 0 ]] || die 'Als root ausführen (oder den Einzeiler mit | sudo bash starten).'
[[ -d /run/systemd/system ]] || die 'systemd muss im LXC/auf dem Server laufen.'
[[ -f /etc/os-release ]] || die '/etc/os-release fehlt'
python3 - <<'PY'
import platform,sys
c=platform.freedesktop_os_release()
v=c.get('VERSION_ID','0').split('.')
if not ((c.get('ID')=='debian' and int(v[0])>=12) or (c.get('ID')=='ubuntu' and tuple(map(int,v[:2]))>=(24,4))):raise SystemExit('Unterstützt: Debian 12+ oder Ubuntu 24.04+')
if sys.version_info<(3,11):raise SystemExit('Python 3.11 oder neuer erforderlich')
PY
command -v flock >/dev/null || die 'flock fehlt (Paket util-linux installieren)'
exec 9>/run/lock/aibutler-install.lock
flock -n 9 || die 'Eine andere Installation, Sicherung oder Wiederherstellung läuft bereits.'
if [[ $MODE == update && ! -f $MANIFEST ]]; then die 'Keine verwaltete Installation gefunden. Zuerst den Installations-Einzeiler ausführen.'; fi
if [[ -e $APP_ROOT && ! -f $APP_ROOT/.managed && -n $(ls -A "$APP_ROOT") ]]; then die "$APP_ROOT enthält eine fremde/manuelle Installation. Nicht überschrieben; siehe Migration in README."; fi
port_available=true
python3 - "$BIND" "$PORT" <<'PYPORT' || port_available=false
import socket,sys
with socket.socket() as connection:
    connection.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    try:connection.bind((sys.argv[1],int(sys.argv[2])))
    except OSError:raise SystemExit(1)
PYPORT
if ! $port_available; then
    if [[ ! -f $MANIFEST || $PORT != "$PREVIOUS_PORT" ]] || ! systemctl is-active --quiet aibutler.service; then
        die "Port $PORT ist bereits belegt. Anderen Port mit --port wählen."
    fi
fi


cleanup() {
    local code=$?
    local recovered=true
    trap - EXIT
    if ! $SUCCESS && $STOPPED; then
        log 'Installation nicht abgeschlossen. Vorherigen Dienst wiederherstellen …'
        systemctl stop aibutler.service >/dev/null 2>&1 || true
        if $CHANGED && [[ -n $BACKUP && -n $OLD_RELEASE ]]; then
            if ! python3 "$OLD_RELEASE/deploy/maintenance.py" restore "$BACKUP"; then
                printf '[Butler] Automatische Wiederherstellung fehlgeschlagen. Backup: %s\n' "$BACKUP" >&2
                code=1
                recovered=false
            fi
            systemctl daemon-reload || true
        fi
        if [[ -n $OLD_RELEASE ]] && $recovered; then systemctl start aibutler.service || true; fi
    fi
    [[ -z $INSTALL_TEMP ]] || rm -rf -- "$INSTALL_TEMP"
    exit "$code"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

missing_packages=()
for package in ca-certificates curl git python3 python3-venv libgomp1; do
    if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) != 'install ok installed' ]]; then missing_packages+=("$package"); fi
done
if ((${#missing_packages[@]})); then
    log 'Fehlende Systempakete installieren …'
    apt-get update
    apt-get install -y --no-install-recommends "${missing_packages[@]}"
else
    log 'Benötigte Systempakete bereits vorhanden.'
fi
if getent passwd aibutler >/dev/null; then
    python3 - "$APP_DATA" <<'PYUSER'
import grp,pwd,sys
u=pwd.getpwnam('aibutler')
try:group=grp.getgrnam('aibutler')
except KeyError:raise SystemExit('Vorhandener Benutzer aibutler hat keine passende Dienstgruppe')
if not 0<u.pw_uid<1000 or u.pw_dir!=sys.argv[1] or u.pw_shell not in ('/usr/sbin/nologin','/sbin/nologin','/bin/false') or u.pw_gid!=group.gr_gid:
    raise SystemExit('Vorhandener Benutzer aibutler passt nicht zur verwalteten Installation; nicht geändert')
PYUSER
else
    useradd --system --user-group --home-dir "$APP_DATA" --shell /usr/sbin/nologin aibutler
fi
install -d -m 755 "$APP_ROOT" "$APP_ROOT/releases"
install -d -m 700 "$APP_CONFIG" /var/backups/aibutler
install -d -o aibutler -g aibutler -m 700 "$APP_DATA" "$APP_DATA/config"
printf 'Schello805/aibutler\n' > "$APP_ROOT/.managed"
INSTALL_TEMP=$(mktemp -d /tmp/aibutler-install.XXXXXXXX)
log "Quellcode laden ($REF) …"
git init -q "$INSTALL_TEMP/source"
git -C "$INSTALL_TEMP/source" remote add origin "$REPOSITORY"
git -C "$INSTALL_TEMP/source" fetch --quiet --depth=1 origin "$REF"
git -C "$INSTALL_TEMP/source" checkout --quiet --detach FETCH_HEAD
SHA=$(git -C "$INSTALL_TEMP/source" rev-parse HEAD)
RELEASE="$APP_ROOT/releases/$(date -u +%Y%m%dT%H%M%SZ)-${SHA:0:12}"
[[ ! -e $RELEASE ]] || die 'Release-Verzeichnis existiert bereits. Kurz warten und erneut versuchen.'
mv "$INSTALL_TEMP/source" "$RELEASE"
for file in backend/main.py deploy/runtime.py deploy/maintenance.py deploy/control.sh deploy/aibutler.service frontend/tailwind.css; do [[ -f $RELEASE/$file ]] || die "Revision enthält keine vollständige Installer-Version: $file"; done
log 'Python-Abhängigkeiten vorbereiten (der bisherige Dienst läuft weiter) …'
runtime_options=()
if $REFRESH_DEPS; then runtime_options+=(--refresh); fi
RUNTIME=$(python3 "$RELEASE/deploy/runtime.py" --root "$APP_ROOT/runtimes" --release "$RELEASE" --speech "$SPEECH" "${runtime_options[@]}")
ln -s "$RUNTIME" "$RELEASE/.venv"
"$RELEASE/.venv/bin/python" -m pip check
"$RELEASE/.venv/bin/python" -m compileall -q "$RELEASE/backend"
log 'Automatische Tests der neuen Version …'
(cd "$RELEASE" && env -i PATH="$PATH" LANG=C.UTF-8 DATA_DIR="$INSTALL_TEMP/test-data" "$RELEASE/.venv/bin/python" -m pytest -q)
# The virtualenv is created at its permanent path; its executable shebangs remain valid.
chmod -R u=rwX,go=rX "$RELEASE"
if [[ ! -f $APP_DATA/config/.env ]]; then
    install -o aibutler -g aibutler -m 600 "$RELEASE/.env.example" "$APP_DATA/config/.env"
    "$RELEASE/.venv/bin/python" - "$APP_DATA/config/.env" <<'PY'
import secrets,sys
from dotenv import set_key
set_key(sys.argv[1],'API_TOKEN',secrets.token_urlsafe(48))
PY
    chown aibutler:aibutler "$APP_DATA/config/.env"
    chmod 600 "$APP_DATA/config/.env"
fi
if $SPEECH && [[ ! -f $APP_DATA/models/whisper-base/model.bin ]]; then
    log 'Öffentliche Sprachmodell-Gewichte laden (Audio wird nicht hochgeladen) …'
    runuser -u aibutler -- env DATA_DIR="$APP_DATA" HF_HOME="$APP_DATA/cache/huggingface" HF_HUB_DISABLE_TELEMETRY=1 "$RELEASE/.venv/bin/python" "$RELEASE/scripts/setup_speech.py"
fi
if $SEMANTIC; then
    log 'Lokales Suchmodell vorbereiten (einmalig ca. 220 MB; keine privaten Inhalte werden übertragen) …'
    runuser -u aibutler -- env DATA_DIR="$APP_DATA" HF_HOME="$APP_DATA/cache/huggingface" HF_HUB_DISABLE_TELEMETRY=1 "$RELEASE/.venv/bin/python" "$RELEASE/scripts/setup_semantic.py"
fi
if [[ -L $APP_ROOT/current && -f $MANIFEST && -f $APP_CONFIG/service.env && -f /etc/systemd/system/aibutler.service ]]; then OLD_RELEASE=$(readlink -f "$APP_ROOT/current"); fi
if [[ -n $OLD_RELEASE ]]; then
    log 'Dienst kurz anhalten und konsistentes Backup erstellen …'
    STOPPED=true
    systemctl stop aibutler.service
    BACKUP=$(python3 "$OLD_RELEASE/deploy/maintenance.py" backup)
    log "Backup erstellt: $BACKUP"
fi
CHANGED=true
# Atomically replace the release symlink; settings/data live outside the checkout.
ln -sfn "$RELEASE" "$APP_ROOT/.current-next"
mv -Tf "$APP_ROOT/.current-next" "$APP_ROOT/current"
python3 - "$MANIFEST" "$REF" "$PORT" "$DOMAIN" "$PROXY_IP" "$SPEECH" "$SHA" "$SEMANTIC" <<'PY'
import json,sys
from pathlib import Path
path,ref,port,domain,proxy,speech,sha,semantic=sys.argv[1:]
Path(path).write_text(json.dumps(dict(ref=ref,port=int(port),domain=domain,proxy_ip=proxy,speech=speech=='true',semantic=semantic=='true',sha=sha),indent=2)+'\n')
PY
cat > "$APP_CONFIG/service.env" <<ENV
BUTLER_ENV_FILE=$APP_DATA/config/.env
DATA_DIR=$APP_DATA
PUBLIC_ORIGIN=$DOMAIN
SPEECH_ENABLED=$SPEECH
SEMANTIC_ENABLED=$SEMANTIC
BIND_ADDRESS=$BIND
BUTLER_PORT=$PORT
TRUSTED_PROXY_IP=${PROXY_IP:-127.0.0.1}
HF_HOME=$APP_DATA/cache/huggingface
HF_HUB_DISABLE_TELEMETRY=1
PYTHONDONTWRITEBYTECODE=1
ENV
chmod 600 "$MANIFEST" "$APP_CONFIG/service.env"
install -m 644 "$RELEASE/deploy/aibutler.service" /etc/systemd/system/aibutler.service
# Stable entry point always executes the active release's control script.
cat > /usr/local/bin/aibutler <<'CONTROL'
#!/usr/bin/env bash
exec bash /opt/aibutler/current/deploy/control.sh "$@"
CONTROL
chmod 755 /usr/local/bin/aibutler
systemctl daemon-reload
STOPPED=true
log 'Neue Version starten und Erreichbarkeit prüfen …'
systemctl restart aibutler.service
healthy=false
for ((attempt=0;attempt<40;attempt++)); do
    if systemctl is-active --quiet aibutler.service && curl --silent --fail --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null; then healthy=true; break; fi
    sleep 1
done
$healthy || die 'Neue Version startet nicht. Siehe journalctl -u aibutler; vorhandene Installation wird zurückgesetzt.'
systemctl enable aibutler.service >/dev/null
if [[ -n $BACKUP ]]; then ln -sfn "$BACKUP" "$APP_ROOT/latest-update-backup"; fi
SUCCESS=true
log "Fertig. Version: ${SHA:0:12} · Dienst: aibutler · Port: $PORT"
if [[ -n $DOMAIN ]]; then
    printf '\nHTTPS-Adresse: %s\nProxy: %s → http://LXC-IP:%s\nDNS, Zertifikat und Proxy separat einrichten. Anmeldung mit deinem Benutzerkonto. Erstes Konto anlegen: aibutler user add DEINE-EMAIL\n' "$DOMAIN" "$PROXY_IP" "$PORT"
else
    printf '\nNur lokal erreichbar. Auf deinem PC einen SSH-Tunnel öffnen:\n  ssh -N -L %s:127.0.0.1:%s root@LXC-IP\nDann im PC-Browser: http://127.0.0.1:%s\n' "$PORT" "$PORT" "$PORT"
fi
printf '\nSpätere Updates: aibutler update\nStatus: aibutler status\nLogs: aibutler logs\nBackups enthalten Zugangsdaten und private Inhalte; außerhalb des LXC sichern.\n'
