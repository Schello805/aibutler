#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
ROOT=/opt/aibutler
PYTHON=$ROOT/current/.venv/bin/python
HELPER=$ROOT/current/deploy/maintenance.py
case "${1:-help}" in
    help|--help|-h)
        cat <<'HELP'
AI Butler verwalten (als root oder mit sudo):
  aibutler update [--domain HOST --proxy-ip IP] [--no-speech|--speech] [--ref REF] [--refresh-deps]
  aibutler status              Dienststatus
  aibutler logs                Letzte 100 Logzeilen und Live-Protokoll
  aibutler restart             Dienst neu starten
  aibutler user add EMAIL [--admin]  Konto anlegen (erstes Konto ist Administrator)
  aibutler user passwd EMAIL   Passwort zurücksetzen
  aibutler user list           Benutzerkonten anzeigen
  aibutler user enable EMAIL   Konto freigeben
  aibutler user disable EMAIL  Konto sperren
  aibutler backup              Konsistentes Backup; Dienst wird kurz angehalten
  aibutler rollback [BACKUP]   Code UND Daten auf Backup-Stand zurücksetzen
  aibutler backups             Verfügbare Backups auflisten
HELP
        exit 0 ;;
esac
[[ $EUID -eq 0 ]] || { printf 'Bitte als root oder mit sudo ausführen.\n' >&2; exit 1; }
command=$1; shift
case "$command" in
    update)
        script=$(mktemp /tmp/aibutler-updater.XXXXXXXX)
        trap 'rm -f -- "$script"' EXIT
        curl --fail --silent --show-error --location https://raw.githubusercontent.com/Schello805/aibutler/main/install.sh --output "$script"
        bash -n "$script"
        bash "$script" --update "$@"
        ;;
    status) systemctl --no-pager --full status aibutler.service ;;
    logs) journalctl -u aibutler.service -n 100 -f ;;
    restart) systemctl restart aibutler.service ;;
    user) "$PYTHON" "$HELPER" user "$@" ;;
    pair) printf 'Kopplung wurde durch Benutzerkonten ersetzt. Erstes Konto: aibutler user add EMAIL\n' >&2; exit 1 ;;
    backups) find /var/backups/aibutler -mindepth 1 -maxdepth 1 -type d -print | sort ;;
    backup|rollback)
        exec 9>/run/lock/aibutler-install.lock
        flock -n 9 || { printf 'Eine andere Installation/Sicherung läuft bereits.\n' >&2; exit 1; }
        active=false
        systemctl is-active --quiet aibutler.service && active=true
        resume() { if $active; then systemctl start aibutler.service; fi; }
        trap resume EXIT
        systemctl stop aibutler.service
        if [[ $command == backup ]]; then
            "$PYTHON" "$HELPER" backup
        else
            target=${1:-$ROOT/latest-update-backup}
            [[ -d $target ]] || { printf 'Kein Update-Backup vorhanden. Pfad angeben: aibutler rollback /var/backups/aibutler/…\n' >&2; exit 1; }
            "$PYTHON" "$HELPER" restore "$target"
            systemctl daemon-reload
            systemctl start aibutler.service
            active=false
            port=$("$ROOT/current/.venv/bin/python" "$ROOT/current/deploy/maintenance.py" port)
            healthy=false
            for ((attempt=0;attempt<40;attempt++)); do
                if curl --silent --fail --max-time 2 "http://127.0.0.1:$port/healthz" >/dev/null; then healthy=true; break; fi
                sleep 1
            done
            $healthy || { printf 'Wiederhergestellter Dienst nicht erreichbar. Siehe aibutler logs.\n' >&2; exit 1; }
            printf 'Wiederherstellung abgeschlossen.\n'
        fi
        ;;
    *) printf 'Unbekannter Befehl: %s\nSiehe: aibutler help\n' "$command" >&2; exit 1 ;;
esac
