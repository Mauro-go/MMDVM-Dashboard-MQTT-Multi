#!/usr/bin/env bash
# MMDVM Dashboard MQTT - bilingual clean installer
# Supports fresh installations and confirmed reinstallation. Never alters MMDVMHost or existing Apache vhosts.
set -Eeuo pipefail
umask 077

APP=${MMDVM_APP_ROOT:-/opt/mmdvm-dashboard-mqtt}
STATE=/var/lib/mmdvm-dashboard
SERVICE_USER=mmdvm-dashboard
WEBROOT=/var/www/html
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
[[ "$APP" == /* && "$APP" != / ]] || { echo "Invalid APP" >&2; exit 2; }

echo "MMDVM Dashboard MQTT"
echo "1) English (default)"
echo "2) Italiano"
read -r -p "Language / Lingua [1]: " CHOICE
case "${CHOICE:-1}" in
  1) LANGUAGE=en ;;
  2) LANGUAGE=it ;;
  *) echo "Invalid choice / Scelta non valida"; exit 1 ;;
esac
msg() { if [[ $LANGUAGE == it ]]; then printf '%s\n' "$2"; else printf '%s\n' "$1"; fi; }
ask() {
  local prompt=$1
  [[ $LANGUAGE == it ]] && prompt=$2
  read -r -p "$prompt" ANSWER
}
die() { msg "$1" "$2" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root." "Eseguire come root."
[[ -f /etc/debian_version ]] || die "Debian/Raspberry Pi OS required." "Richiesto Debian/Raspberry Pi OS."
command -v systemctl >/dev/null && command -v apt-get >/dev/null || die "systemd and apt are required." "Sono richiesti systemd e apt."
for file in backend/app.py backend/mqtt_collector.py backend/telegram_bot.py backend/telegram_settings.py backend/admin_api.py config/dashboard.ini.example frontend/index.html scripts/setup-https.sh; do
  [[ -f "$SRC/$file" ]] || die "Missing source: $file" "File sorgente mancante: $file"
done
# An interrupted first installation can leave APP and/or service units behind.
REINSTALL=false
if [[ -e $APP || -e /etc/systemd/system/mmdvm-dashboard-mqtt.service ||
      -e /etc/systemd/system/mmdvm-mqtt-collector.service ||
      -e /etc/systemd/system/mmdvm-telegram-bot.service ||
      -e /etc/apache2/conf-available/mmdvm-dashboard-mqtt-api.conf ]]; then
  msg 'Existing or partial installation detected.' 'Installazione esistente o parziale rilevata.'
  ask 'Overwrite application files, preserving settings and data? [y/N]: ' \
      'Sovrascrivere i file applicativi conservando configurazioni e dati? [s/N]: '
  case "${ANSWER:-}" in
    y|Y|s|S) REINSTALL=true ;;
    *) die 'Installation cancelled.' 'Installazione annullata.' ;;
  esac
fi
if command -v apache2ctl >/dev/null; then
  msg "Apache is already installed; existing websites will be preserved." "Apache è già installato; i siti esistenti saranno preservati."
else
  msg "Apache will be installed." "Apache verrà installato."
fi

# Questions are grouped by deployment and broker choice; no changes before confirmation.
CALLSIGN=
MMDVM_INI=/etc/MMDVM-Host.ini
DETECTED_MMDVM_INI=
if [[ -f "$SRC/scripts/detect-mmdvm-config.py" ]]; then
  DETECTED_MMDVM_INI=$(python3 "$SRC/scripts/detect-mmdvm-config.py" 2>/dev/null) || true
fi
if [[ -n $DETECTED_MMDVM_INI ]]; then
  MMDVM_INI=$DETECTED_MMDVM_INI
fi
NODE_ID=
AGENT_PREFIX=
MQTT_USER=
MQTT_PASSWORD=
MQTT_TLS=false
BROKER_MODE=remote
MOSQ_NEW=false

choice() {
  local prompt_en=$1 prompt_it=$2 valid=$3 default=$4
  while :; do
    ask "$prompt_en" "$prompt_it"
    ANSWER=${ANSWER:-$default}
    [[ $ANSWER =~ $valid ]] && return 0
    msg 'Invalid selection. Try again.' 'Scelta non valida. Riprova.'
  done
}
field() {
  local en=$1 it=$2 default=$3 pattern=$4
  while :; do
    ask "$en" "$it"
    ANSWER=${ANSWER:-$default}
    [[ $ANSWER =~ $pattern ]] && return 0
    msg 'Invalid value. Try again.' 'Valore non valido. Riprova.'
  done
}
secret() {
  local prompt
  if [[ $LANGUAGE == it ]]; then prompt='Password MQTT (non visualizzata, vuota se anonimo): '; else prompt='MQTT password (hidden, blank if anonymous): '; fi
  read -r -s -p "$prompt" MQTT_PASSWORD; printf '\n'
}
msg 'Dashboard connection: 1) Local MMDVM.ini  2) Remote MQTT Agent' 'Collegamento dashboard: 1) MMDVM.ini locale  2) Agent MQTT remoto'
choice 'Connection mode [1]: ' 'Modalità collegamento [1]: ' '^[12]$' 1
if [[ $ANSWER == 1 ]]; then
  DEPLOYMENT_MODE=local
  while :; do
    field "MMDVMHost config file [$MMDVM_INI]: " "File configurazione MMDVMHost [$MMDVM_INI]: " "$MMDVM_INI" '^/[^[:cntrl:]]+$'
    MMDVM_INI=$ANSWER
    [[ -r $MMDVM_INI ]] && break
    msg 'File not readable. Try again.' 'File non leggibile. Riprova.'
  done
else
  DEPLOYMENT_MODE=remote
  msg 'The node ID must match the Agent configuration.' "L'identificativo nodo deve coincidere con quello dell'Agent."
  field 'Agent node ID: ' 'Identificativo nodo Agent: ' '' '^[A-Za-z0-9_.-]+$'
  NODE_ID=${ANSWER,,}
  msg 'Default Agent prefix: mmdvm/<node_id>/agent/v1 (lowercase).' 'Prefisso Agent predefinito: mmdvm/<node_id>/agent/v1 (minuscolo).'
  field "Agent prefix [mmdvm/$NODE_ID/agent/v1]: " "Prefisso Agent [mmdvm/$NODE_ID/agent/v1]: " "mmdvm/$NODE_ID/agent/v1" '^[A-Za-z0-9_.-]+(/[A-Za-z0-9_.-]+)*$'
  AGENT_PREFIX=$ANSWER
fi

msg 'Broker location: 1) Local to dashboard  2) Remote/existing broker' 'Posizione broker: 1) Locale sulla dashboard  2) Broker remoto/esistente'
choice 'Broker location [2]: ' 'Posizione broker [2]: ' '^[12]$' 2
if [[ $ANSWER == 1 ]]; then
  BROKER_MODE=local
  HOST=127.0.0.1
  MQTT_PORT=1883
  if dpkg-query -W -f='${Status}' mosquitto 2>/dev/null | grep -qx 'install ok installed'; then
    if [[ -e /etc/mosquitto/conf.d/mmdvm-dashboard.conf && ! -f /etc/mosquitto/mmdvm-dashboard.passwd ]] ||
       [[ -e /etc/mosquitto/mmdvm-dashboard.passwd && ! -f /etc/mosquitto/conf.d/mmdvm-dashboard.conf ]]; then
      die 'Incomplete dashboard Mosquitto configuration; manual inspection required.' 'Configurazione Mosquitto della dashboard incompleta; necessaria verifica manuale.'
    fi
    if [[ -f /etc/mosquitto/conf.d/mmdvm-dashboard.conf && -f /etc/mosquitto/mmdvm-dashboard.passwd ]]; then
      msg 'Existing dashboard Mosquitto configuration detected; it will not be changed.' 'Configurazione Mosquitto della dashboard già presente; non verrà modificata.'
      systemctl is-active --quiet mosquitto || die 'Existing Mosquitto is inactive; check manually.' 'Mosquitto esistente non attivo; verificare manualmente.'
    else
      msg 'Mosquitto is installed but dashboard configuration is missing.' 'Mosquitto installato, ma configurazione dashboard assente.'
      if find /etc/mosquitto/conf.d -maxdepth 1 -type f ! -name README -print -quit 2>/dev/null | grep -q .; then
        msg 'Other Mosquitto configurations detected; preserving existing broker.' 'Rilevate altre configurazioni Mosquitto; broker esistente preservato.'
        systemctl is-active --quiet mosquitto || die 'Existing Mosquitto is inactive; check manually.' 'Mosquitto esistente non attivo; verificare manualmente.'
      else
        # Only complete an existing broker with the standard Debian configuration.
        MOSQ_MAIN=$(grep -Ev '^[[:space:]]*(#|$)' /etc/mosquitto/mosquitto.conf 2>/dev/null | sed 's/[[:space:]]*$//' | sort)
        MOSQ_STANDARD=$(printf '%s\n'           'persistence true'           'persistence_location /var/lib/mosquitto/'           'log_dest file /var/log/mosquitto/mosquitto.log'           'include_dir /etc/mosquitto/conf.d' | sort)
        [[ "$MOSQ_MAIN" == "$MOSQ_STANDARD" ]] ||
          die 'Existing Mosquitto configuration is not standard; manual inspection required.' 'Configurazione Mosquitto esistente non standard; necessaria verifica manuale.'
        MOSQ_NEW=true
        msg 'Incomplete Mosquitto setup detected; dashboard configuration will be completed.' 'Rilevata configurazione Mosquitto incompleta; verrà completata quella della dashboard.'
      fi
    fi
  else
    MOSQ_NEW=true
    msg 'Mosquitto will be installed with authenticated LAN listener on port 1883.' 'Mosquitto verrà installato con accesso LAN autenticato sulla porta 1883.'
    [[ ! -e /etc/mosquitto/conf.d/mmdvm-dashboard.conf && ! -e /etc/mosquitto/mmdvm-dashboard.passwd ]] || die 'Mosquitto configuration collision.' 'Conflitto configurazione Mosquitto.'
  fi
else
  field 'MQTT broker host/IP: ' 'Host/IP broker MQTT: ' '' '^[A-Za-z0-9._:-]+$'
  HOST=$ANSWER
  field 'MQTT port [1883]: ' 'Porta MQTT [1883]: ' 1883 '^[0-9]{1,5}$'
  MQTT_PORT=$((10#$ANSWER))
  (( MQTT_PORT >= 1 && MQTT_PORT <= 65535 )) || die 'Port out of range.' 'Porta fuori intervallo.'
fi
if [[ $MOSQ_NEW == true ]]; then
  field 'New Mosquitto username [mmdvm-dashboard]: ' 'Nuovo utente Mosquitto [mmdvm-dashboard]: ' mmdvm-dashboard '^[A-Za-z0-9_.-]+$'
  MQTT_USER=$ANSWER
  while :; do
    secret
    [[ -n $MQTT_PASSWORD ]] && break
    msg 'Password required for new broker.' 'Password obbligatoria per il nuovo broker.'
  done
else
  ask 'MQTT username (blank if anonymous): ' 'Username MQTT (vuoto se anonimo): '
  MQTT_USER=$ANSWER
  secret
  choice 'Use TLS? 1) No 2) Yes [1]: ' 'Usare TLS? 1) No 2) Sì [1]: ' '^[12]$' 1
  [[ $ANSWER == 2 ]] && MQTT_TLS=true
fi
if [[ $DEPLOYMENT_MODE == local ]]; then
  field 'MMDVM JSON topic [mmdvm/+/json]: ' 'Topic JSON MMDVM [mmdvm/+/json]: ' 'mmdvm/+/json' '^[A-Za-z0-9_+.#/-]+$'
  TOPIC=$ANSWER
else
  # Agent data is received through AGENT_PREFIX/config, /telemetry, /status.
  TOPIC='mmdvm/+/json'
  msg 'Agent messages use the prefix above; legacy JSON topic stays at default.' 'I messaggi Agent usano il prefisso sopra; il topic JSON tradizionale resta predefinito.'
fi
DETECTED_IP=$(hostname -I 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i !~ /^127[.]/ && $i !~ /:/){print $i;exit}}')
DETECTED_IP=${DETECTED_IP:-127.0.0.1}
msg 'Enter the address used in your browser to reach this dashboard.' "Inserisci l'indirizzo che userai nel browser per accedere alla dashboard."
field "Dashboard HTTPS address [$DETECTED_IP]: " "Indirizzo HTTPS dashboard [$DETECTED_IP]: " "$DETECTED_IP" '^[A-Za-z0-9._:-]+$'
HTTPS_NAME=$ANSWER

# The active Apache site keeps its own DocumentRoot and HTTPS configuration.
# Add ONLY /api/ proxy rules, without creating another listener or vhost.
if command -v ss >/dev/null; then
  if [[ $REINSTALL != true ]]; then
    [[ -z $(ss -H -ltn "sport = :8089") ]] || die "Backend port 8089 is in use." "Porta backend 8089 occupata."
  fi
fi
if [[ -d /etc/apache2 ]]; then
  if [[ $REINSTALL != true ]] && grep -RIEq '^[[:space:]]*(ProxyPass|Alias|Redirect|<Location)[[:space:]]+["'\''"]?/api(/|["'\''"]|[[:space:]>])' /etc/apache2/sites-enabled /etc/apache2/conf-enabled 2>/dev/null; then
    die "Apache already defines /api/. Review its configuration manually." "Apache gestisce già /api/. Controlla manualmente la configurazione."
  fi
fi

# Refuse all existing web asset collisions unless explicitly approved.
declare -a COLLISIONS=()
while IFS= read -r -d '' file; do
  relative=${file#"$SRC/frontend/"}
  if [[ -e "$WEBROOT/$relative" || -L "$WEBROOT/$relative" ]]; then COLLISIONS+=("$relative"); fi
done < <(find "$SRC/frontend" -type f -print0)
if (( ${#COLLISIONS[@]} )); then
  msg "The following web files already exist:" "I seguenti file web esistono già:"
  printf '  %s\n' "${COLLISIONS[@]}"
  msg "All conflicting files will be backed up before replacement." "Tutti i file in conflitto saranno salvati prima della sostituzione."
  ask "Back up and overwrite these files? [y/N]: " "Creare backup e sovrascrivere questi file? [s/N]: "
  if [[ $LANGUAGE == it ]]; then
    [[ ${ANSWER:-} == [sS] ]] || die "Cancelled." "Annullato."
  else
    [[ ${ANSWER:-} == [yY] ]] || die "Cancelled." "Annullato."
  fi
fi
msg "Existing Apache sites and DocumentRoot directives will NOT be modified." "I siti Apache e le DocumentRoot esistenti NON saranno modificati."
msg "Frontend index.html -> /var/www/html/index.html. Existing Apache VirtualHosts and ports are preserved." "Frontend index.html -> /var/www/html/index.html. VirtualHost e porte Apache esistenti saranno preservati."
msg "The /api/ reverse proxy will be enabled at Apache server level (all VirtualHosts)." "Il proxy /api/ verrà abilitato a livello globale Apache (tutti i VirtualHost)."
msg "HTTPS will be configured automatically if needed." "HTTPS verrà configurato automaticamente se necessario."
msg "--- Installation summary ---" "--- Riepilogo installazione ---"
msg "Mode: $DEPLOYMENT_MODE | Broker: $BROKER_MODE ($HOST:$MQTT_PORT) | TLS: $MQTT_TLS" "Modalità: $DEPLOYMENT_MODE | Broker: $BROKER_MODE ($HOST:$MQTT_PORT) | TLS: $MQTT_TLS"
msg "Agent node: $NODE_ID | Prefix: $AGENT_PREFIX | JSON topic: $TOPIC" "Nodo Agent: $NODE_ID | Prefisso: $AGENT_PREFIX | Topic JSON: $TOPIC"
msg "HTTPS: https://$HTTPS_NAME/ | MQTT user: ${MQTT_USER:-anonymous}" "HTTPS: https://$HTTPS_NAME/ | Utente MQTT: ${MQTT_USER:-anonimo}"
ask "Proceed with installation? [y/N]: " "Procedere con l'installazione? [s/N]: "
if [[ $LANGUAGE == it ]]; then
  [[ ${ANSWER:-} == [sS] ]] || exit 0
else
  [[ ${ANSWER:-} == [yY] ]] || exit 0
fi

if dpkg-query -W -f='${Status}' apache2 2>/dev/null | grep -qx 'install ok installed'; then
  ask 'Reuse installed Apache? [Y/n]: ' 'Riutilizzare Apache installato? [S/n]: '
else
  ask 'Install Apache? [Y/n]: ' 'Installare Apache? [S/n]: '
fi
case "${ANSWER,,}" in ''|y|yes|s|si) ;; *) die 'Apache required; cancelled.' 'Apache necessario; annullato.' ;; esac

MQTT_SECRET_FILE=$(mktemp)
chmod 0600 "$MQTT_SECRET_FILE"
printf '%s' "$MQTT_PASSWORD" > "$MQTT_SECRET_FILE"
unset MQTT_PASSWORD
trap 'rm -f -- "${MQTT_SECRET_FILE:-}" "${MOSQ_PASSWD_TMP:-}"' EXIT
export DEBIAN_FRONTEND=noninteractive
apt-get update
if dpkg-query -W -f='${Status}' apache2 2>/dev/null | grep -qx 'install ok installed'; then
  apt-get install -y curl ca-certificates openssl python3 python3-flask python3-werkzeug python3-psutil python3-paho-mqtt
else
  apt-get install -y curl ca-certificates apache2 openssl python3 python3-flask python3-werkzeug python3-psutil python3-paho-mqtt
fi
if [[ $MOSQ_NEW == true ]]; then apt-get install -y mosquitto mosquitto-clients; fi


if [[ $MOSQ_NEW == true ]]; then
  install -d -m 0755 /etc/mosquitto/conf.d
  # Create hashed credentials without exposing the password in process arguments.
  MOSQ_PASSWD_TMP=$(mktemp /etc/mosquitto/.mmdvm-dashboard.passwd.XXXXXXXX)
  chmod 0600 "$MOSQ_PASSWD_TMP"
  printf '%s:%s\n' "$MQTT_USER" "$(cat "$MQTT_SECRET_FILE")" > "$MOSQ_PASSWD_TMP"
  mosquitto_passwd -U "$MOSQ_PASSWD_TMP"
  chown mosquitto:mosquitto "$MOSQ_PASSWD_TMP"
  chmod 0600 "$MOSQ_PASSWD_TMP"
  mv -- "$MOSQ_PASSWD_TMP" /etc/mosquitto/mmdvm-dashboard.passwd
  MOSQ_PASSWD_TMP=
  cat > /etc/mosquitto/conf.d/mmdvm-dashboard.conf <<'MOSQ'
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/mmdvm-dashboard.passwd
MOSQ
  chmod 0644 /etc/mosquitto/conf.d/mmdvm-dashboard.conf
  systemctl enable --now mosquitto
  systemctl restart mosquitto
  systemctl is-active --quiet mosquitto || die 'Mosquitto failed to start.' 'Mosquitto non avviato.'
fi

id "$SERVICE_USER" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$SERVICE_USER"
# Install shared DMR database updater.
install -d -o root -g root -m 0755 "$APP/scripts"
install -o root -g root -m 0755 "$SRC/scripts/update-dmr-ids.sh" "$APP/scripts/update-dmr-ids.sh"
install -o root -g root -m 0644 "$SRC/systemd/mmdvm-dmr-ids-update@.service" /etc/systemd/system/mmdvm-dmr-ids-update@.service
install -o root -g root -m 0644 "$SRC/systemd/mmdvm-dmr-ids-update@.timer" /etc/systemd/system/mmdvm-dmr-ids-update@.timer
install -d -o root -g "$SERVICE_USER" -m 2770 "$APP/config"
# Il canale di aggiornamento proviene dal pacchetto, mai dal nome della cartella.
[[ -f "$SRC/UPDATE_REPOSITORY" && ! -L "$SRC/UPDATE_REPOSITORY" ]] || die 'Missing UPDATE_REPOSITORY' 'UPDATE_REPOSITORY mancante'
UPDATE_REPOSITORY=$(cat "$SRC/UPDATE_REPOSITORY")
case "$UPDATE_REPOSITORY" in
  Mauro-go/MMDVM-Dashboard-MQTT-Multi) ;;
  *) die 'Invalid update repository' 'Repository aggiornamenti non valido' ;;
esac
if [[ -f "$APP/config/update-repository" ]]; then
  [[ $(cat "$APP/config/update-repository") == "$UPDATE_REPOSITORY" ]] || die     'Existing installation belongs to a different update channel.'     'Installazione esistente associata a un altro canale di aggiornamento.'
else
  install -o root -g root -m 0644 "$SRC/UPDATE_REPOSITORY" "$APP/config/update-repository"
fi
# Metadati dell'installazione per eventuali componenti updater Multi.
if [[ ! -e "$APP/config/update-runtime.json" ]]; then
  APP="$APP" python3 - <<'PYRUNTIME'
import json,os
from pathlib import Path
p=Path(os.environ['APP'])/'config/update-runtime.json'
p.write_text(json.dumps({'mode':'standard','port':8089,'services':['mmdvm-dashboard-mqtt.service','mmdvm-mqtt-collector.service','mmdvm-telegram-bot.service']},indent=2)+'\n')
p.chmod(0o644)
PYRUNTIME
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$APP/data" "$STATE"
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 "$APP/data/databases"
for dir in backend frontend admin; do cp -a "$SRC/$dir" "$APP/"; done
install -o root -g root -m 0644 "$SRC/VERSION" "$APP/VERSION"
chown -R root:root "$APP/backend" "$APP/frontend" "$APP/admin"
find "$APP/backend" "$APP/frontend" "$APP/admin" -type d -exec chmod 0755 {} +
find "$APP/backend" "$APP/frontend" "$APP/admin" -type f -exec chmod 0644 {} +
# Preserve the real dashboard configuration on reinstall (including partial installs).
if [[ ! -f "$APP/config/dashboard.ini" ]]; then
  install -o root -g "$SERVICE_USER" -m 0660 "$SRC/config/dashboard.ini.example" "$APP/config/dashboard.ini"
  CONFIG_CREATED=true
else
  CONFIG_CREATED=false
  msg 'Existing dashboard.ini preserved.' 'dashboard.ini esistente conservato.'
fi

if [[ ! -e "$APP/data/mqtt-settings.json" ]]; then
if [[ $CONFIG_CREATED == false ]]; then cp -p -- "$APP/config/dashboard.ini" "$APP/config/dashboard.ini.preserve-tmp"; fi
APP="$APP" CALLSIGN="$CALLSIGN" HOST="$HOST" MQTT_PORT="$MQTT_PORT" TOPIC="$TOPIC" MMDVM_INI="$MMDVM_INI" DEPLOYMENT_MODE="$DEPLOYMENT_MODE" NODE_ID="$NODE_ID" AGENT_PREFIX="$AGENT_PREFIX" MQTT_USER="$MQTT_USER" MQTT_TLS="$MQTT_TLS" MQTT_SECRET_FILE="$MQTT_SECRET_FILE" python3 - <<'PYSET'
import os, json, configparser
from pathlib import Path
root = Path(os.environ['APP'])
p = root/'config/dashboard.ini'
c = configparser.ConfigParser(interpolation=None)
c.read(p)
c.set('repeater', 'callsign', os.environ['CALLSIGN'])
if not c.has_section('deployment'):
    c.add_section('deployment')
c.set('deployment', 'mode', os.environ['DEPLOYMENT_MODE'])
c.set('deployment', 'mmdvm_ini', os.environ['MMDVM_INI'])
c.set('deployment', 'node_id', os.environ['NODE_ID'])
c.set('deployment', 'agent_topic_prefix', os.environ['AGENT_PREFIX'])
m = Path(os.environ['MMDVM_INI'])
if not c.has_section('paths'): c.add_section('paths')
c.set('paths', 'mmdvm_dir', str(m.parent))
c.set('paths', 'mmdvm_file', m.name)
c.set('paths', 'dmr_ids', str(root/'data/databases/DMRIds.dat'))
c.set('paths', 'tg_list_dir', str(root/'data/databases'))
c.set('paths', 'tg_list_file', 'TG_List.csv')
with p.open('w') as f: c.write(f)
q = root/'data/mqtt-settings.json'
q.write_text(json.dumps(dict(host=os.environ['HOST'], port=int(os.environ['MQTT_PORT']), topic=os.environ['TOPIC'], username=os.environ['MQTT_USER'], password=Path(os.environ['MQTT_SECRET_FILE']).read_text(), tls=os.environ['MQTT_TLS']=='true'), indent=2)+'\n')
q.chmod(0o600)
PYSET
if [[ $CONFIG_CREATED == false ]]; then
  mv -f -- "$APP/config/dashboard.ini.preserve-tmp" "$APP/config/dashboard.ini"
fi
else
  msg 'Existing configuration and MQTT credentials preserved.' 'Configurazione e credenziali MQTT esistenti conservate.'
fi
chown root:"$SERVICE_USER" "$APP/config/dashboard.ini"
chmod 0660 "$APP/config/dashboard.ini"
if [[ -f "$APP/data/mqtt-settings.json" ]]; then
  chown "$SERVICE_USER:$SERVICE_USER" "$APP/data/mqtt-settings.json"
fi
rm -f -- "$MQTT_SECRET_FILE" "${MOSQ_PASSWD_TMP:-}"
MQTT_SECRET_FILE=
MOSQ_PASSWD_TMP=
trap - EXIT

# Local databases are managed on the DASHBOARD host in both deployment modes.
DB_DIR="$APP/data/databases"
TG_CSV="$DB_DIR/TG_List.csv"
DMR_IDS="$DB_DIR/DMRIds.dat"
if [[ ! -e "$TG_CSV" ]]; then
  printf 'MODE,TS,TG,LABEL,URL\nDMR,1,222,BM-IT,\n' > "$TG_CSV"
  chown "$SERVICE_USER:$SERVICE_USER" "$TG_CSV"
  chmod 0640 "$TG_CSV"
  msg "TG_List.csv created." "TG_List.csv creato."
fi

# DMRIds.dat format: numeric ID, callsign, optional name (whitespace-separated).
# Download into a temporary file, validate it, and publish atomically.
# Preserve an existing database during installation; weekly updates replace it after validation.
if [[ ! -s "$DMR_IDS" ]]; then
  "$APP/scripts/update-dmr-ids.sh" "$APP" || die "DMR database download failed." "Download database DMR fallito."
else
  msg "Existing DMR ID database preserved." "Database ID DMR esistente conservato."
fi


# Private Telegram settings are intentionally not copied from any running installation.
# The admin UI creates them when configured.
if [[ ! -f "$STATE/session-secret" ]]; then openssl rand -hex 32 > "$STATE/session-secret"; fi
if [[ ! -f "$STATE/admin.json" && ! -f "$STATE/setup-token" ]]; then openssl rand -hex 24 > "$STATE/setup-token"; fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$STATE"
chmod 0750 "$STATE"
chmod 0600 "$STATE"/*

# Back up every collision, and copy only source files; never delete other websites.
STAMP=$(date +%Y%m%d-%H%M%S)
while IFS= read -r -d '' file; do
  relative=${file#"$SRC/frontend/"}
  target="$WEBROOT/$relative"
  install -d -m 0755 "$(dirname "$target")"
  if [[ -e $target || -L $target ]]; then
    cp -a -- "$target" "$target.bak-mmdvm-$STAMP"
  fi
  cp -- "$file" "$target"
  chmod 0644 "$target"
done < <(find "$SRC/frontend" -type f -print0)

cat > /etc/systemd/system/mmdvm-mqtt-collector.service <<EOF
[Unit]
Description=MMDVM Dashboard MQTT Collector
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP/backend
ExecStart=/usr/bin/python3 -u $APP/backend/mqtt_collector.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/mmdvm-dashboard-mqtt.service <<EOF
[Unit]
Description=MMDVM Dashboard MQTT Backend
Wants=network-online.target mmdvm-mqtt-collector.service
After=network-online.target
[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP/backend
Environment=MMDVM_DASHBOARD_PORT=8089
Environment=MMDVM_DASHBOARD_STATE_DIR=$STATE
ExecStart=/usr/bin/python3 -u $APP/backend/app.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data $APP/config $STATE
[Install]
WantedBy=multi-user.target
EOF
cat > /etc/systemd/system/mmdvm-telegram-bot.service <<EOF
[Unit]
Description=MMDVM MQTT Telegram Bot
Wants=network-online.target
After=network-online.target
[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$APP/backend
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-/etc/default/mmdvm-telegram-bot
ExecStart=/usr/bin/python3 $APP/backend/telegram_bot.py
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
PrivateTmp=true
[Install]
WantedBy=multi-user.target
EOF

# API proxy only. Apache continues to serve /var/www/html/index.html itself.
# Existing HTTPS, ports, virtual hosts and DocumentRoot directives are unchanged.
# HTTPS_TRANSACTION_V3: register cleanup BEFORE changing Apache configuration.
HTTPS_CREATED=0
HTTPS_COMMITTED=0
HTTPS_RESULT_FILE=$(mktemp)
chmod 0600 "$HTTPS_RESULT_FILE"
https_install_cleanup() {
  local rc=$?
  trap - EXIT INT TERM
  rm -f -- "${HTTPS_RESULT_FILE:-}"
  if (( rc != 0 && HTTPS_COMMITTED == 0 && REINSTALL != true )); then
    if (( HTTPS_CREATED == 1 )); then
      a2dissite mmdvm-dashboard-mqtt-ssl >/dev/null 2>&1 || true
      rm -f -- /etc/apache2/sites-available/mmdvm-dashboard-mqtt-ssl.conf
      rm -f -- /etc/apache2/ssl/mmdvm-dashboard-mqtt/server.key /etc/apache2/ssl/mmdvm-dashboard-mqtt/server.crt
      rmdir -- /etc/apache2/ssl/mmdvm-dashboard-mqtt 2>/dev/null || true
    fi
    a2disconf mmdvm-dashboard-mqtt-api >/dev/null 2>&1 || true
    rm -f -- /etc/apache2/conf-available/mmdvm-dashboard-mqtt-api.conf
    if apache2ctl configtest >/dev/null 2>&1 && systemctl is-active --quiet apache2; then
      systemctl reload apache2 || true
    fi
    msg "Apache changes rolled back; other installation files may remain." \
        "Modifiche Apache annullate; altri file di installazione potrebbero rimanere." >&2
  fi
  exit "$rc"
}
trap https_install_cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
if [[ ! -e /etc/apache2/conf-available/mmdvm-dashboard-mqtt-api.conf ]]; then
  cat > /etc/apache2/conf-available/mmdvm-dashboard-mqtt-api.conf <<EOF
ProxyPreserveHost On
ProxyPass /api/ http://127.0.0.1:8089/api/
ProxyPassReverse /api/ http://127.0.0.1:8089/api/
EOF
else
  msg 'Existing Apache API proxy preserved.' 'Proxy API Apache esistente conservato.'
fi
a2enmod proxy proxy_http ssl >/dev/null
a2enconf mmdvm-dashboard-mqtt-api >/dev/null

if ! LANGUAGE="$LANGUAGE" HTTPS_RESULT_FILE="$HTTPS_RESULT_FILE" \
    bash "$SRC/scripts/setup-https.sh" "$HTTPS_NAME" "$WEBROOT"; then
  die "HTTPS preparation failed." "Preparazione HTTPS non riuscita."
fi
HTTPS_RESULT=$(cat "$HTTPS_RESULT_FILE")
case "$HTTPS_RESULT" in
  created)
    HTTPS_CREATED=1
    a2ensite mmdvm-dashboard-mqtt-ssl >/dev/null || \
      die "Cannot enable HTTPS site." "Impossibile abilitare il sito HTTPS."
    ;;
  reused)
    msg "Existing HTTPS site preserved." "Sito HTTPS esistente conservato."
    ;;
  *) die "Invalid HTTPS module result." "Risultato modulo HTTPS non valido." ;;
esac
apache2ctl configtest || die "Apache configuration invalid." "Configurazione Apache non valida."
# Do not restart an existing Apache instance if its reload fails.
if systemctl is-active --quiet apache2; then
  systemctl reload apache2 || die "Apache reload failed; no restart attempted." "Reload Apache fallito; nessun riavvio tentato."
else
  systemctl start apache2 || die "Cannot start Apache." "Impossibile avviare Apache."
fi
# Start backend/collector only after Apache configuration is accepted.
systemctl daemon-reload

systemctl enable --now mmdvm-mqtt-collector.service mmdvm-dashboard-mqtt.service
if [[ $REINSTALL == true ]]; then
  systemctl restart mmdvm-mqtt-collector.service mmdvm-dashboard-mqtt.service
fi
# Do not enable Telegram until configured by the administrator.
if [[ $REINSTALL != true ]]; then
  systemctl disable mmdvm-telegram-bot.service >/dev/null 2>&1 || true
fi
systemctl enable --now "mmdvm-dmr-ids-update@$(basename "$APP").timer"
HTTPS_COMMITTED=1
trap - EXIT INT TERM
rm -f -- "$HTTPS_RESULT_FILE"
if [[ $REINSTALL == true ]]; then
  msg 'Telegram service state preserved.' 'Stato del servizio Telegram conservato.'
else
  msg 'Telegram service installed but disabled.' 'Servizio Telegram installato ma disabilitato.'
fi

printf '\n%s\n' '============================================================'
msg 'MMDVM DASHBOARD MQTT - INSTALLATION COMPLETED' 'MMDVM DASHBOARD MQTT - INSTALLAZIONE COMPLETATA'
printf '%s\n' '============================================================'
msg "Dashboard:       https://$HTTPS_NAME/" "Dashboard:       https://$HTTPS_NAME/"
msg "Administration:  https://$HTTPS_NAME/api/admin/" "Amministrazione: https://$HTTPS_NAME/api/admin/"
msg 'Services: mmdvm-dashboard-mqtt, mmdvm-mqtt-collector' 'Servizi: mmdvm-dashboard-mqtt, mmdvm-mqtt-collector'
msg 'Telegram: installed, disabled until configured' 'Telegram: installato, disabilitato fino alla configurazione'
printf '\n'
if [[ -f "$STATE/setup-token" && ! -f "$STATE/admin.json" ]]; then
  msg 'FIRST ADMINISTRATOR ACCESS' 'PRIMO ACCESSO AMMINISTRATORE'
  printf '%s\n' '------------------------------------------------------------'
  printf 'Token: %s\n' "$(<"$STATE/setup-token")"
  msg 'Save this token securely. Use it to create the Admin account.' \
      'Conserva il token in modo sicuro. Usalo per creare l’account Admin.'
  msg "Token file: $STATE/setup-token" "File del token: $STATE/setup-token"
else
  msg 'Admin setup token not displayed (already used or unavailable).' \
      'Token iniziale non mostrato (già utilizzato o non disponibile).'
fi
printf '%s\n' '============================================================'

if [[ $DEPLOYMENT_MODE == remote ]]; then
  msg 'Install agent/install.sh on the repeater. Enter the EXACT broker, node ID and prefix shown above (case-sensitive).' \
      'Installare agent/install.sh sul ripetitore. Inserire ESATTAMENTE broker, nodo e prefisso indicati sopra (maiuscole/minuscole contano).'
fi
