#!/usr/bin/env bash
# Installazione infrastruttura updater per una dashboard ESISTENTE.
# Uso: bash install-updater-existing.sh /percorso/app PORTA servizio1.service [servizio2.service ...]
set -Eeuo pipefail
[[ $EUID == 0 ]] || { echo 'ERRORE: root richiesto' >&2; exit 1; }
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
(( $# >= 3 )) || { echo "Uso: $0 /percorso/installazione PORTA servizio1.service [altri.service...]" >&2; exit 2; }
APP="$(realpath -e -- "$1")"; shift
PORT="$1"; shift
[[ "$APP" == /* && "$APP" != / && "$APP" != *$'\n'* && "$APP" != *' '* ]] || { echo 'Percorso non valido' >&2; exit 2; }
[[ "$PORT" =~ ^[0-9]+$ ]] && (( PORT >= 1 && PORT <= 65535 )) || { echo 'Porta non valida' >&2; exit 2; }
[[ -f "$APP/backend/app.py" && -d "$APP/frontend" && -d "$APP/admin" && -f "$APP/VERSION" && -d "$APP/data" ]] || { echo 'Installazione non valida' >&2; exit 1; }
[[ -f "$SRC/UPDATE_REPOSITORY" && ! -L "$SRC/UPDATE_REPOSITORY" && -f "$APP/config/update-repository" && ! -L "$APP/config/update-repository" ]] || { echo 'Origine aggiornamenti assente' >&2; exit 1; }
REPO="$(cat "$APP/config/update-repository")"
[[ "$REPO" == "$(cat "$SRC/UPDATE_REPOSITORY")" ]] || { echo 'ERRORE: il repository dei sorgenti è diverso da quello installato' >&2; exit 1; }
case "$REPO" in Mauro-go/MMDVM-Dashboard-MQTT-Multi) ;; *) echo 'Repository non autorizzato' >&2; exit 1;; esac
SERVICES=()
for unit in "$@"; do
  [[ "$unit" =~ ^[a-zA-Z0-9_.@-]+\.service$ ]] || { echo "Servizio non valido: $unit" >&2; exit 2; }
  systemctl show "$unit" -p LoadState --value | grep -qx loaded || { echo "Servizio non installato: $unit" >&2; exit 1; }
  unitdata="$(systemctl show "$unit" -p ExecStart -p WorkingDirectory)"
  [[ "$unitdata" == *"$APP"* ]] || { echo "Servizio $unit non associato a $APP" >&2; exit 1; }
  SERVICES+=("$unit")
done
[[ -f "$SRC/scripts/update_worker.py" && -f "$SRC/scripts/update_runtime.py" ]] || { echo 'Sorgenti updater incompleti' >&2; exit 1; }
ID="$(printf %s "$APP" | sha256sum | cut -c1-12)"
UNIT="mmdvm-multi-update-$ID"
if [[ -e "/etc/systemd/system/$UNIT.service" || -e "/etc/systemd/system/$UNIT.timer" ]]; then
  echo "Updater $UNIT già presente: interrompo per evitare sovrascritture" >&2
  exit 1
fi
printf 'Installazione: %s\nRepository: %s\nPorta: %s\nServizi: %s\nUnità: %s\n' "$APP" "$REPO" "$PORT" "${SERVICES[*]}" "$UNIT"
# Verifica eventuale runtime PRIMA di modificare file o servizi.
APP="$APP" PORT="$PORT" SERVICES_JSON="$(printf '%s\n' "${SERVICES[@]}")" python3 - <<'PYPREFLIGHT'
import json, os
from pathlib import Path
p=Path(os.environ['APP'])/'config/update-runtime.json'
expected={'mode':'multi' if 'mmdvm-dashboard-multi.service' in os.environ['SERVICES_JSON'].splitlines() else 'standard', 'port':int(os.environ['PORT']), 'services':os.environ['SERVICES_JSON'].splitlines()}
if p.is_symlink(): raise SystemExit('Runtime symlink non consentito')
if p.exists():
    existing=json.loads(p.read_text())
    if existing['port'] != expected['port'] or existing['services'] != expected['services']:
        raise SystemExit('Runtime esistente diverso da parametri: interrompo')
PYPREFLIGHT
read -r -p 'Installa updater per QUESTA installazione? Scrivi CONFERMO: ' ANSWER
[[ "$ANSWER" == CONFERMO ]] || exit 0
install -d -o root -g root -m 0755 "$APP/scripts"
for f in "$SRC"/scripts/update_*.py; do install -o root -g root -m 0644 "$f" "$APP/scripts/$(basename "$f")"; done
APP="$APP" PORT="$PORT" SERVICES_JSON="$(printf '%s\n' "${SERVICES[@]}")" python3 - <<'PYMETA'
import json, os
from pathlib import Path
app=Path(os.environ['APP'])
path=app/'config/update-runtime.json'
services=os.environ['SERVICES_JSON'].splitlines()
new={'mode':'multi' if 'mmdvm-dashboard-multi.service' in services else 'standard','port':int(os.environ['PORT']),'services':services}
if path.is_symlink(): raise SystemExit('Configurazione runtime symlink non consentita')
if path.exists():
    old=json.loads(path.read_text())
    if old['port'] != new['port'] or old['services'] != new['services']: raise SystemExit('Configurazione runtime preesistente diversa: interrompo')
else:
    path.write_text(json.dumps(new,indent=2)+'\n')
    path.chmod(0o644)
PYMETA
cat > "/etc/systemd/system/$UNIT.service" <<EOF
[Unit]
Description=MMDVM updater for $APP
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=root
Group=root
WorkingDirectory=$APP/scripts
Environment=MMDVM_APP_ROOT=$APP
ExecStart=/usr/bin/python3 $APP/scripts/update_worker.py
TimeoutStartSec=600
UMask=0077
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=full
ReadWritePaths=$APP
EOF
install -o root -g root -m 0644 "$SRC/systemd/mmdvm-multi-update.timer" "/etc/systemd/system/$UNIT.timer"
systemctl daemon-reload
systemctl enable --now "$UNIT.timer"
echo "Updater installato: $UNIT.timer. La richiesta già presente sarà elaborata al prossimo avvio del timer."
