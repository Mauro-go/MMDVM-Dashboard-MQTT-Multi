#!/usr/bin/env bash
# MMDVM Dashboard MQTT Multi - dedicated Debian installer
# --check (default): no system changes; --install: explicit installation
set -Eeuo pipefail
umask 077
SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
APP=/opt/mmdvm-dashboard-multi
STATE=/var/lib/mmdvm-dashboard-multi
WEBROOT=/var/www/html
USER_NAME=mmdvm-dashboard-multi
PORT=8090
MQTT_PORT=1883
SITE=mmdvm-dashboard-multi
MODE=${1:---check}
[[ "$MODE" == --check || "$MODE" == --install ]] || { echo 'Usage: install-multi.sh [--check|--install]' >&2; exit 2; }
fail(){ echo "ERRORE: $*" >&2; exit 1; }
info(){ echo "==> $*"; }
[[ $EUID == 0 ]] || fail 'Eseguire come root.'
[[ -f /etc/debian_version ]] || fail 'Richiesto Debian.'
for f in backend/app.py backend/multi.py backend/multi_worker.py backend/mqtt_collector.py backend/admin_api.py config/dashboard.ini.example frontend/index.html admin/multi.html; do
  [[ -f "$SRC/$f" ]] || fail "File mancante: $f"
done
[[ ! -e "$APP" && ! -L "$APP" ]] || fail "Directory installazione già presente: $APP"
for unit in mmdvm-dashboard-multi mmdvm-multi-collector mmdvm-multi-telegram; do
  [[ ! -e /etc/systemd/system/$unit.service ]] || fail "Servizio già presente: $unit"
done
for path in /etc/apache2/sites-available/$SITE.conf /etc/apache2/ssl/$SITE /etc/mosquitto/conf.d/$SITE.conf /etc/mosquitto/$SITE.passwd; do
  [[ ! -e "$path" && ! -L "$path" ]] || fail "Conflitto: $path"
done
# Dedicated server only: do not change existing Apache sites or Mosquitto installations.
if dpkg-query -W -f='${Status}' mosquitto 2>/dev/null | grep -qx 'install ok installed'; then
  fail 'Mosquitto già installato: configurazione manuale richiesta; nessuna modifica.'
fi
if [[ -d /etc/apache2 ]] || dpkg-query -W -f='${Status}' apache2 2>/dev/null | grep -qx 'install ok installed'; then
  fail 'Apache già presente: usare Debian dedicato pulito; nessuna modifica.'
fi
if [[ -e "$WEBROOT/index.html" ]]; then
  fail 'Webroot già occupata: installazione non sicura.'
fi
if command -v ss >/dev/null; then
  for port in 80 443 "$PORT" "$MQTT_PORT"; do
    [[ -z $(ss -H -ltn "sport = :$port") ]] || fail "Porta $port già occupata"
  done
fi
if [[ $MODE == --check ]]; then
  info 'Controlli preliminari superati. Nessuna modifica eseguita.'
  info 'Installazione effettiva: bash install-multi.sh --install (SOLO su Debian dedicato pulito).'
  exit 0
fi
read -r -p 'Nominativo primo ripetitore (es. IR3ZZO): ' CALLSIGN
CALLSIGN=${CALLSIGN%$'\r'}
[[ "$CALLSIGN" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{1,19}$ ]] || fail 'Nominativo non valido'
read -r -p "Node ID Agent [$CALLSIGN]: " NODE_ID
NODE_ID=${NODE_ID:-$CALLSIGN}
[[ "$NODE_ID" =~ ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$ ]] || fail 'Node ID non valido'
read -r -p "Prefisso Agent [mmdvm/$NODE_ID/agent/v1]: " PREFIX
PREFIX=${PREFIX:-mmdvm/$NODE_ID/agent/v1}
[[ "$PREFIX" =~ ^[A-Za-z0-9_-]+(/[A-Za-z0-9_-]+)*$ ]] || fail 'Prefisso Agent non valido'
read -r -p 'Username MQTT [mmdvm-dashboard]: ' MQTT_USER
MQTT_USER=${MQTT_USER:-mmdvm-dashboard}
[[ "$MQTT_USER" =~ ^[A-Za-z0-9_.-]+$ ]] || fail 'Username MQTT non valido'
read -r -s -p 'Password MQTT (non visibile): ' MQTT_PASSWORD; echo
[[ -n "$MQTT_PASSWORD" ]] || fail 'Password MQTT obbligatoria'
read -r -s -p 'Ripeti password MQTT: ' MQTT_CONFIRM; echo
[[ "$MQTT_PASSWORD" == "$MQTT_CONFIRM" ]] || fail 'Password non corrispondente'
unset MQTT_CONFIRM
DETECTED_IP=$(hostname -I 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i !~ /^127[.]/ && $i !~ /:/){print $i;exit}}')
read -r -p "Indirizzo HTTPS [${DETECTED_IP:-127.0.0.1}]: " HTTPS_NAME
HTTPS_NAME=${HTTPS_NAME:-${DETECTED_IP:-127.0.0.1}}
[[ "$HTTPS_NAME" =~ ^[A-Za-z0-9._-]+$ ]] || fail 'Indirizzo HTTPS non valido'
cat <<SUMMARY

Riepilogo:
  App:       $APP
  Ripetitore: $CALLSIGN
  Agent ID:  $NODE_ID
  Agent:     $PREFIX
  Broker:    $HTTPS_NAME:$MQTT_PORT (TCP, autenticazione obbligatoria)
  HTTPS:     https://$HTTPS_NAME/ (certificato autofirmato)
  Backend:   127.0.0.1:$PORT
  Utente MQTT: $MQTT_USER

SUMMARY
read -r -p 'Confermi installazione su Debian dedicato? Scrivi INSTALLA: ' CONFIRM
[[ "$CONFIRM" == INSTALLA ]] || { info 'Annullato.'; exit 0; }
# Recheck before modifying system.
[[ ! -e "$APP" ]] || fail 'Installazione comparsa durante la conferma'
export DEBIAN_FRONTEND=noninteractive
info 'Installazione pacchetti'
apt-get update
apt-get install -y apache2 openssl ca-certificates curl mosquitto mosquitto-clients python3 python3-flask python3-werkzeug python3-psutil python3-paho-mqtt
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$APP" --shell /usr/sbin/nologin "$USER_NAME"
install -d -o root -g root -m 0755 "$APP"
for dir in backend frontend admin; do cp -a "$SRC/$dir" "$APP/$dir"; done
install -d -o root -g "$USER_NAME" -m 2770 "$APP/config"
install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$APP/data" "$APP/data/databases" "$STATE"
install -o root -g "$USER_NAME" -m 0660 "$SRC/config/dashboard.ini.example" "$APP/config/dashboard.ini"
# Do not copy live credentials, data, or backup files from the source tree.
find "$APP/backend" "$APP/frontend" "$APP/admin" -type f \( -name '*.bak-*' -o -name '*.pyc' \) -delete
find "$APP/backend" "$APP/frontend" "$APP/admin" -type d -name __pycache__ -prune -exec rm -rf {} +
chown -R root:root "$APP/backend" "$APP/frontend" "$APP/admin"
find "$APP/backend" "$APP/frontend" "$APP/admin" -type d -exec chmod 0755 {} +
find "$APP/backend" "$APP/frontend" "$APP/admin" -type f -exec chmod 0644 {} +
export APP CALLSIGN NODE_ID PREFIX MQTT_USER MQTT_PASSWORD
python3 - <<'PY'
import configparser,json,os
from pathlib import Path
root=Path(os.environ['APP'])
c=configparser.ConfigParser(interpolation=None)
p=root/'config/dashboard.ini'; c.read(p)
c.set('repeater','callsign',os.environ['CALLSIGN'])
c.set('deployment','mode','remote')
c.set('deployment','node_id',os.environ['NODE_ID'])
c.set('deployment','agent_topic_prefix',os.environ['PREFIX'])
c.set('paths','dmr_ids',str(root/'data/databases/DMRIds.dat'))
c.set('paths','tg_list_dir',str(root/'data/databases'))
c.set('paths','tg_list_file','TG_List.csv')
with p.open('w') as h: c.write(h)
settings=dict(host='127.0.0.1',port=1883,topic=f"mmdvm/{os.environ['CALLSIGN']}/json",username=os.environ['MQTT_USER'],password=os.environ['MQTT_PASSWORD'],tls=False)
q=root/'data/mqtt-settings.json';q.write_text(json.dumps(settings,indent=2)+'\n');q.chmod(0o600)
PY
chown "$USER_NAME:$USER_NAME" "$APP/data/mqtt-settings.json"
chmod 0660 "$APP/config/dashboard.ini"
printf 'MODE,TS,TG,LABEL,URL\nDMR,1,222,BM-IT,\n' > "$APP/data/databases/TG_List.csv"
chown "$USER_NAME:$USER_NAME" "$APP/data/databases/TG_List.csv"
chmod 0640 "$APP/data/databases/TG_List.csv"
openssl rand -hex 32 > "$STATE/session-secret"
openssl rand -hex 24 > "$STATE/setup-token"
chown -R "$USER_NAME:$USER_NAME" "$STATE"
chmod 0600 "$STATE/session-secret" "$STATE/setup-token"
# Mosquitto password hashing: never put cleartext secret in process argv.
TMPPASS=$(mktemp /etc/mosquitto/.multi-pass.XXXXXXXX)
trap 'rm -f -- "${TMPPASS:-}"' EXIT
printf '%s:%s\n' "$MQTT_USER" "$MQTT_PASSWORD" > "$TMPPASS"
mosquitto_passwd -U "$TMPPASS"
chown mosquitto:mosquitto "$TMPPASS"
chmod 0600 "$TMPPASS"
mv -- "$TMPPASS" "/etc/mosquitto/$SITE.passwd"
TMPPASS=
unset MQTT_PASSWORD
cat > "/etc/mosquitto/conf.d/$SITE.conf" <<MOSQ
listener 1883 0.0.0.0
allow_anonymous false
password_file /etc/mosquitto/$SITE.passwd
MOSQ
chmod 0644 "/etc/mosquitto/conf.d/$SITE.conf"
# Webroot only on a dedicated, previously empty server.
cp -a "$APP/frontend/." "$WEBROOT/"
find "$WEBROOT" -type d -exec chmod 0755 {} +
find "$WEBROOT" -type f -exec chmod 0644 {} +
# Separate dedicated HTTPS vhost; no dependence on Standard installer.
CERTDIR="/etc/apache2/ssl/$SITE"
install -d -m 0700 "$CERTDIR"
if [[ "$HTTPS_NAME" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then SAN="IP:$HTTPS_NAME"; else SAN="DNS:$HTTPS_NAME"; fi
openssl req -x509 -nodes -newkey rsa:3072 -sha256 -days 825 \
  -keyout "$CERTDIR/server.key" -out "$CERTDIR/server.crt" \
  -subj "/CN=$HTTPS_NAME" -addext "subjectAltName=$SAN" >/dev/null 2>&1
chmod 0600 "$CERTDIR/server.key"
chmod 0644 "$CERTDIR/server.crt"
cat > "/etc/apache2/sites-available/$SITE.conf" <<APACHE
<VirtualHost *:443>
    ServerName $HTTPS_NAME
    DocumentRoot $WEBROOT
    SSLEngine on
    SSLCertificateFile $CERTDIR/server.crt
    SSLCertificateKeyFile $CERTDIR/server.key
    ProxyPreserveHost On
    # Proxy all routes, including /<callsign>/ for Multi workers.
    ProxyPass / http://127.0.0.1:$PORT/
    ProxyPassReverse / http://127.0.0.1:$PORT/
    <Directory $WEBROOT>
        Require all granted
        Options -Indexes
        AllowOverride None
    </Directory>
</VirtualHost>
APACHE
cat > "/etc/systemd/system/mmdvm-multi-collector.service" <<UNIT
[Unit]
Description=MMDVM Multi MQTT Collector
Wants=network-online.target mosquitto.service
After=network-online.target mosquitto.service
[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP/backend
Environment=MMDVM_NODE_CONFIG=$APP/config/dashboard.ini
Environment=MMDVM_NODE_DATA=$APP/data
Environment=MMDVM_DASHBOARD_STATE_DIR=$STATE
ExecStart=/usr/bin/python3 -u $APP/backend/mqtt_collector.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data $STATE
[Install]
WantedBy=multi-user.target
UNIT
cat > "/etc/systemd/system/mmdvm-dashboard-multi.service" <<UNIT
[Unit]
Description=MMDVM Dashboard MQTT Multi
Wants=network-online.target mosquitto.service mmdvm-multi-collector.service
After=network-online.target mosquitto.service
[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP/backend
Environment=MMDVM_NODE_CONFIG=$APP/config/dashboard.ini
Environment=MMDVM_NODE_DATA=$APP/data
Environment=MMDVM_DASHBOARD_STATE_DIR=$STATE
Environment=MMDVM_DASHBOARD_PORT=$PORT
ExecStart=/usr/bin/python3 -u $APP/backend/app.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data $APP/config $STATE
[Install]
WantedBy=multi-user.target
UNIT
cat > "/etc/systemd/system/mmdvm-multi-telegram.service" <<UNIT
[Unit]
Description=MMDVM Dashboard Multi Telegram Notifications
Wants=network-online.target mosquitto.service
After=network-online.target mosquitto.service
[Service]
Type=simple
User=$USER_NAME
Group=$USER_NAME
WorkingDirectory=$APP/backend
Environment=MMDVM_NODE_CONFIG=$APP/config/dashboard.ini
Environment=MMDVM_NODE_DATA=$APP/data
Environment=MMDVM_DASHBOARD_STATE_DIR=$STATE
ExecStart=/usr/bin/python3 -u $APP/backend/telegram_bot.py
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true
ReadWritePaths=$APP/data $STATE
[Install]
WantedBy=multi-user.target
UNIT
info 'Validazione Apache e avvio servizi'
a2enmod proxy proxy_http ssl >/dev/null
a2ensite "$SITE" >/dev/null
apache2ctl configtest
systemctl daemon-reload
systemctl enable --now mosquitto
systemctl restart mosquitto
systemctl enable --now mmdvm-multi-collector.service mmdvm-dashboard-multi.service mmdvm-multi-telegram.service
systemctl enable --now apache2
systemctl reload apache2
systemctl is-active --quiet mosquitto
systemctl is-active --quiet apache2
systemctl is-active --quiet mmdvm-dashboard-multi
systemctl is-active --quiet mmdvm-multi-collector
systemctl is-active --quiet mmdvm-multi-telegram
info "Dashboard: https://$HTTPS_NAME/"
info "Admin: https://$HTTPS_NAME/api/admin/"
echo
echo "=============================================="
echo "       CONFIGURAZIONE AMMINISTRATORE"
echo "=============================================="
echo "Setup token: $(cat "$STATE/setup-token")"
echo "Conservare il token in luogo sicuro."
echo "Non pubblicarlo e non condividerlo."
echo "=============================================="
info 'ATTENZIONE: MQTT su TCP/1883 non è cifrato. Usare LAN/VPN o configurare TLS prima di esporlo su Internet.'
