<!-- DEVELOPMENT_UPDATE_NOTES_V1 -->
# MMDVM Dashboard MQTT Multi
<img width="1917" height="1077" alt="Screenshot MMDVM Dashboard MQTT" src="https://github.com/user-attachments/assets/3a8af506-f042-42d1-a3e5-58cbe43487f2" />


**[Italiano](#italiano) · [English](#english)**

Dashboard web per ripetitori radioamatoriali MMDVMHost con raccolta eventi MQTT, telemetria tramite Agent remoto, amministrazione e notifiche Telegram opzionali.

Web dashboard for MMDVMHost amateur-radio repeaters, with MQTT event collection, remote Agent telemetry, administration and optional Telegram notifications.

> **Versione documentata / Documented version:** branch `main`, installer universali. La sintassi degli installer è stata verificata; non è ancora dichiarato un collaudo end-to-end completo. / Installer syntax has been checked; a complete end-to-end installation test is not yet claimed.

---



## Italiano

### Rilevamento automatico configurazione MMDVMHost

Gli installer della Dashboard (modalità locale) e dell'Agent MQTT
cercano il servizio systemd MMDVMHost e ricavano il percorso completo
del file di configurazione dal comando ExecStart.

Sono supportati nomi come `MMDVM.ini`, `MMDVM-Host.ini` e nomi
personalizzati. Il percorso rilevato viene proposto all'utente,
che può confermarlo oppure modificarlo manualmente.

Nell'Admin della Dashboard locale il percorso completo è gestito
da un unico campo nella sezione Collegamento MMDVM.
In modalità remota il percorso è gestito dall'Agent sul ripetitore.

Il rilevamento non modifica MMDVMHost né i suoi servizi.


### 1. Panoramica e architettura

Il progetto comprende:

- **Dashboard:** frontend statico servito da Apache, backend/API Flask sulla porta locale `8089`, collector MQTT, pannello Admin e bot Telegram opzionale.
- **Agent (facoltativo):** servizio di sola lettura installato sulla macchina del ripetitore. Legge un insieme limitato di parametri da `MMDVM.ini`, lo stato dei servizi systemd e le informazioni di sistema; pubblica i dati su MQTT. Non riceve comandi e non modifica MMDVMHost.
- **Broker MQTT:** Mosquitto locale o un broker esistente, anche su un terzo server. Dashboard e Agent devono poter raggiungere lo stesso broker per scambiarsi la telemetria Agent.

**Topologie supportate:** Dashboard sul ripetitore (lettura locale di `MMDVM.ini`); Dashboard su un'altra macchina e Agent sul ripetitore; broker sulla Dashboard, sul ripetitore oppure su un server separato. Gli eventi legacy MMDVMHost (`mmdvm/+/json`, salvo personalizzazione) sono distinti dai messaggi Agent.

Il progetto **non installa né riconfigura MMDVMHost** e non abilita automaticamente la pubblicazione MQTT da parte di MMDVMHost. La telemetria Agent e gli eventi radio legacy sono due flussi differenti.

### 2. Requisiti e raccomandazioni

- Debian 12/13 o Raspberry Pi OS basato su Debian, `apt`, `systemd`, accesso `root` e connessione Internet per i pacchetti.
- Per la Dashboard: Apache e Python 3; l'installer installa i pacchetti necessari, incluso Apache se assente.
- Per l'Agent: `MMDVM.ini` leggibile, Python 3 e accesso al broker MQTT.
- Accesso MQTT sulla porta configurata (normalmente `1883`) tra le macchine; accesso web HTTPS alla Dashboard.
- Repository **completo** per la Dashboard; per il solo Agent è disponibile lo ZIP autonomo.

**È preferibile una macchina nuova**, soprattutto per evitare conflitti web, ma non è indispensabile. L'installer Dashboard rifiuta una precedente installazione in `/opt/mmdvm-dashboard-mqtt`, servizi omonimi o una configurazione Apache API omonima; non è un aggiornamento in-place. Su un server con altri siti Apache, conserva i VirtualHost esistenti, ma copia il frontend in `/var/www/html/`, può sostituire file omonimi **solo dopo conferma e backup**, e abilita il proxy `/api/` globalmente. Verificare attentamente i conflitti prima di procedere. Non garantisce una ripresa automatica dopo un'installazione interrotta.

L'installer Agent rifiuta file o servizi Agent già esistenti e non li sovrascrive, anche se identici. Per un Agent preesistente usare la configurazione attuale e confrontare manualmente broker, Node ID e prefisso.

### 3. Scaricare il repository

Per installare la versione ufficiale:

```bash
apt update && apt install -y git
git clone -b main https://github.com/Mauro-go/MMDVM-Dashboard-MQTT.git /opt/MMDVM-Dashboard-MQTT
cd /opt/MMDVM-Dashboard-MQTT
```

In alternativa scaricare l'archivio completo del repository dal branch selezionato e decomprimerlo. **Non basta** un archivio contenente soltanto `install.sh`, `agent/install.sh` e `scripts/setup-https.sh`: servono anche `backend/`, `frontend/`, `admin/`, `config/` e i file Agent.

### 4. Installazione automatica Dashboard

Eseguire come `root` dalla directory del repository:

```bash
cd /opt/MMDVM-Dashboard-MQTT
bash install.sh
```

La procedura è interattiva (English/Italiano). Chiede:

1. **Modalità:** `MMDVM.ini` locale oppure **Agent MQTT remoto**.
2. In modalità locale: percorso reale di `MMDVM.ini` (es. `/etc/MMDVM.ini` oppure `/opt/cfg/MMDVM.ini`). In modalità remota: **Node ID** e **prefisso MQTT Agent**.
3. **Broker:** locale alla Dashboard oppure remoto/esistente; indirizzo, porta, credenziali e TLS dove previsti. Un Mosquitto già installato non viene riconfigurato; se manca e si sceglie un nuovo broker locale, viene installato.
4. **Topic legacy MMDVMHost:** in modalità locale è configurabile (predefinito `mmdvm/+/json`). In modalità remota rimane separato dal prefisso Agent.
5. **Indirizzo HTTPS** usato nel browser; riepilogo e conferma prima di applicare le modifiche.

**Node ID e prefisso non sono nomi imposti.** Per esempio `pippo` può essere un Node ID; il prefisso può essere `mmdvm/pippo/agent/v1`, `mmdvm/pippo` o un altro percorso MQTT valido. Il Node ID inserito viene normalizzato in minuscolo; il **prefisso personalizzato mantiene le maiuscole/minuscole inserite**. Annotare i valori esatti e usarli anche sull'Agent.

Il programma installa i servizi `mmdvm-dashboard-mqtt.service` e `mmdvm-mqtt-collector.service`. Installa anche `mmdvm-telegram-bot.service`, inizialmente disabilitato. Il backend usa `127.0.0.1:8089` dietro il proxy Apache `/api/`.

**Apache e HTTPS:** se Apache non è presente viene installato. I VirtualHost già esistenti non vengono riscritti. Se non è presente un VirtualHost HTTPS, lo script può crearne uno con **certificato autofirmato**; il browser potrebbe mostrare un avviso. Se un sito HTTPS esiste già, lo riutilizza senza verificare automaticamente che il dominio, la DocumentRoot e le regole del sito corrispondano alla Dashboard. Controllare manualmente che il VirtualHost HTTPS serva `/var/www/html` e inoltri `/api/`. Per accesso pubblico utilizzare un certificato attendibile e configurare opportunamente firewall e accessi.

Indirizzi previsti, **se il VirtualHost HTTPS serve il frontend installato**:

- Dashboard: `https://INDIRIZZO/`
- Amministrazione: `https://INDIRIZZO/api/admin/`
- API: `https://INDIRIZZO/api/`

### 5. Installazione automatica Agent sul ripetitore

Scaricare sul ripetitore il pacchetto autonomo **[MMDVM-MQTT-Agent.zip](MMDVM-MQTT-Agent.zip)** direttamente dal branch ufficiale. Non è necessario clonare l'intero repository.

Eseguire come `root`:

```bash
apt update && apt install -y wget unzip
mkdir -p /root/mmdvm-agent-installer
cd /root/mmdvm-agent-installer
wget -O MMDVM-MQTT-Agent.zip https://github.com/Mauro-go/MMDVM-Dashboard-MQTT/raw/refs/heads/main/MMDVM-MQTT-Agent.zip
unzip MMDVM-MQTT-Agent.zip
bash install.sh
```

Lo ZIP contiene `install.sh`, `agent.py`, `mmdvm-agent.service`, `agent.json.example` e `broker.json.example`. Non contiene password o configurazioni personali.

In alternativa, clonare il repository completo e avviare `bash install.sh` dalla directory `agent/`.

L'installer chiede lingua, posizione del broker, percorso `MMDVM.ini`, Node ID (propone il callsign letto da `[General] Callsign`), prefisso MQTT, intervallo di pubblicazione (minimo 5 secondi), credenziali ed eventuale TLS. Se il broker locale non esiste e viene scelto come nuovo, installa Mosquitto con autenticazione; se esiste, ne conserva la configurazione.

Il servizio installato è **`mmdvm-agent.service`**, non `mmdvm-mqtt-agent.service`. L'Agent non riavvia MMDVMHost né i gateway radio. L'installer interrompe l'operazione se rileva un Agent già installato: non è una procedura di aggiornamento.

### 6. Corrispondenza MQTT: regola fondamentale

Dashboard e Agent **devono usare lo stesso broker raggiungibile, porta, credenziali compatibili e lo stesso prefisso MQTT Agent, carattere per carattere**. I topic MQTT distinguono maiuscole e minuscole.

| Campo | Esempio Dashboard | Esempio Agent |
| --- | --- | --- |
| Node ID | `pippo` | `pippo` |
| Prefisso Agent | `mmdvm/pippo/agent/v1` | `mmdvm/pippo/agent/v1` |
| Broker | `10.0.0.100:1883` | `10.0.0.100:1883` |
| Topic eventi legacy | `mmdvm/+/json` | Non è il prefisso Agent |

I messaggi Agent vengono pubblicati sotto il prefisso configurato, con suffissi come `/config`, `/telemetry` e `/status`. Se l'Agent usa un broker locale sul ripetitore (`127.0.0.1:1883`) e la Dashboard usa un broker remoto, **non sono automaticamente lo stesso broker**: occorre usare un broker comune oppure configurare esplicitamente un bridge MQTT. La presenza di una connessione MQTT non dimostra che i messaggi arrivino alla Dashboard.

### 7. Configurazione e primo accesso

| Percorso | Funzione |
| --- | --- |
| `/opt/mmdvm-dashboard-mqtt/config/dashboard.ini` | Configurazione generale, modalità, Node ID, prefisso Agent |
| `/opt/mmdvm-dashboard-mqtt/data/mqtt-settings.json` | Broker e topic legacy; contiene credenziali |
| `/opt/mmdvm-dashboard-mqtt/data/telegram-settings.json` | Impostazioni Telegram, se configurate |
| `/opt/mmdvm-dashboard-mqtt/data/events.sqlite3` | Eventi raccolti; conservazione prevista di 48 ore |
| `/var/lib/mmdvm-dashboard/setup-token` | Token iniziale amministratore, se ancora presente |
| `/opt/mmdvm-agent/agent.json` | Node ID, prefisso, percorso MMDVM.ini e intervallo |
| `/opt/mmdvm-agent/broker.json` | Broker e credenziali Agent |

Dopo l'installazione Dashboard, aprire `/api/admin/` sul sito HTTPS e completare la configurazione dell'amministratore. Al termine dell’installazione automatica il token iniziale viene mostrato nel riepilogo finale, se non è già stato creato un amministratore. È recuperabile localmente, come root:

```bash
cat /var/lib/mmdvm-dashboard/setup-token
```

Proteggere token, password MQTT e file JSON privati. Il bot Telegram è opzionale: configurarlo dal pannello amministrativo e abilitarne il servizio solo dopo aver verificato le impostazioni.

### 8. Installazione manuale (avanzata)

La procedura seguente è una **traccia operativa**, non un sostituto testato dell'installer automatico. È destinata a chi sa gestire permessi, systemd, Apache e Mosquitto. Effettuare backup e controllare conflitti e percorsi prima di copiare file. **Non eseguire su un'installazione già attiva senza un piano di migrazione.**

**Dashboard — pacchetti e directory:**

```bash
apt update
apt install -y apache2 openssl curl python3 python3-flask python3-werkzeug python3-psutil python3-paho-mqtt
id mmdvm-dashboard >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin mmdvm-dashboard
install -d -m 2770 -o root -g mmdvm-dashboard /opt/mmdvm-dashboard-mqtt/config
install -d -m 0750 -o mmdvm-dashboard -g mmdvm-dashboard /opt/mmdvm-dashboard-mqtt/data /opt/mmdvm-dashboard-mqtt/data/databases /var/lib/mmdvm-dashboard
```

Dal repository completo, copiare `backend/`, `admin/` e `frontend/` in `/opt/mmdvm-dashboard-mqtt/`; copiare i file statici di `frontend/` nella DocumentRoot Apache prescelta **solo dopo averne controllato i conflitti**. Creare `/opt/mmdvm-dashboard-mqtt/config/dashboard.ini` partendo da `config/dashboard.ini.example` e impostare almeno `[deployment]` (`mode`, `mmdvm_ini`, `node_id`, `agent_topic_prefix`) e `[paths]` secondo l'ambiente. Configurare il file privato MQTT, per esempio:

```json
{
  "host": "10.0.0.100",
  "port": 1883,
  "topic": "mmdvm/+/json",
  "username": "UTENTE_MQTT",
  "password": "PASSWORD_DA_SOSTITUIRE",
  "tls": false
}
```

Salvarlo in `/opt/mmdvm-dashboard-mqtt/data/mqtt-settings.json`, proprietario `mmdvm-dashboard`, permessi `0600`. Proteggere `dashboard.ini` con gruppo `mmdvm-dashboard`; predisporre i segreti Admin in `/var/lib/mmdvm-dashboard` (la generazione iniziale di `session-secret` e `setup-token` è descritta in `install.sh`). Creare i file database necessari o lasciare che i componenti li inizializzino dove previsto.

**Servizi Dashboard:** predisporre due unit systemd con `WorkingDirectory=/opt/mmdvm-dashboard-mqtt/backend`, utente e gruppo `mmdvm-dashboard`, `Restart=always`, avviando rispettivamente:

```text
/usr/bin/python3 -u /opt/mmdvm-dashboard-mqtt/backend/mqtt_collector.py
/usr/bin/python3 -u /opt/mmdvm-dashboard-mqtt/backend/app.py
```

Per il backend impostare `MMDVM_DASHBOARD_PORT=8089` e `MMDVM_DASHBOARD_STATE_DIR=/var/lib/mmdvm-dashboard`; concedere scrittura solo a `data/`, `config/` e alla directory di stato secondo necessità. Il servizio Telegram è opzionale. È consigliabile prendere come riferimento le unit generate da `install.sh` anziché improvvisare opzioni systemd.

**Apache/HTTPS:** abilitare `proxy`, `proxy_http`, `ssl`; nel contesto appropriato del sito aggiungere:

```apache
ProxyPass /api/ http://127.0.0.1:8089/api/
ProxyPassReverse /api/ http://127.0.0.1:8089/api/
```

Configurare un VirtualHost HTTPS che serva il frontend statico; usare un certificato valido o uno autofirmato in rete privata. Verificare con `apache2ctl configtest`, quindi ricaricare Apache e abilitare i servizi con `systemctl daemon-reload` e `systemctl enable --now`. Evitare conflitti con altri siti che usano `/api/`.

**Agent — installazione manuale:**

```bash
apt update
apt install -y python3 python3-paho-mqtt python3-psutil
install -d -m 0700 /opt/mmdvm-agent
install -m 0644 agent/agent.py /opt/mmdvm-agent/agent.py
install -m 0644 agent/mmdvm-agent.service /etc/systemd/system/mmdvm-agent.service
```

Creare `/opt/mmdvm-agent/agent.json`:

```json
{
  "node_id": "pippo",
  "topic_prefix": "mmdvm/pippo/agent/v1",
  "mmdvm_ini": "/opt/cfg/MMDVM.ini",
  "broker_settings_file": "/opt/mmdvm-agent/broker.json",
  "interval_seconds": 10
}
```

Creare `/opt/mmdvm-agent/broker.json`:

```json
{
  "host": "10.0.0.100",
  "port": 1883,
  "username": "UTENTE_MQTT",
  "password": "PASSWORD_DA_SOSTITUIRE",
  "tls": false
}
```

Adattare tutti i valori all'impianto, assegnare proprietà `root:root` e permessi `0600` a entrambi i JSON. Verificare prima di attivare:

```bash
python3 -B /opt/mmdvm-agent/agent.py --config /opt/mmdvm-agent/agent.json --dry-run
systemctl daemon-reload
systemctl enable --now mmdvm-agent
```

**Broker manuale:** se si installa Mosquitto, configurare un listener autenticato accessibile alle macchine necessarie, con credenziali e firewall adeguati. Non esporre `1883` indiscriminatamente a Internet; valutare TLS per reti non fidate. Non modificare la configurazione di un broker condiviso senza verificarne l'impatto.

### 9. Diagnostica e manutenzione

```bash
# Dashboard
systemctl status mmdvm-dashboard-mqtt mmdvm-mqtt-collector --no-pager
journalctl -u mmdvm-mqtt-collector -n 50 --no-pager
journalctl -u mmdvm-dashboard-mqtt -n 50 --no-pager
apache2ctl configtest

# Agent (sul ripetitore)
systemctl status mmdvm-agent --no-pager
journalctl -u mmdvm-agent -n 50 --no-pager
```

Se i dati radio arrivano ma la telemetria Agent no, confrontare `agent_topic_prefix` in `dashboard.ini` con `topic_prefix` in `agent.json`, poi verificare broker, porta, credenziali e routing/bridge. Se il frontend risponde ma l'API no, controllare proxy `/api/`, backend `8089` e log systemd. Il collector conserva gli eventi per circa 48 ore e ne esegue la pulizia periodica.

### 10. Sicurezza, backup e limiti

- Non pubblicare mai `mqtt-settings.json`, `broker.json`, token Telegram, database o segreti Admin su GitHub.
- Il programma non gestisce MMDVMHost né riavvia i servizi radio.
- Prima di intervenire su un sistema usato, salvare configurazioni Apache, MQTT, frontend, directory applicative e unit systemd.
- L'installer Dashboard **non è idempotente** e non riprende automaticamente installazioni interrotte. L'installer Agent rifiuta un Agent preesistente.
- L'installazione manuale richiede competenze amministrative e verifica locale; i passaggi descrivono i componenti principali, non sostituiscono un collaudo completo.

---

## English

### Automatic MMDVMHost configuration detection

The Dashboard installer (local mode) and MQTT Agent installer
inspect the MMDVMHost systemd service and obtain the full
configuration file path from its ExecStart command.

Filenames such as `MMDVM.ini`, `MMDVM-Host.ini`, and custom names
are supported. The detected path is suggested to the user,
who can confirm or manually override it.

In the local Dashboard Admin, the complete path is managed
through one field in the MMDVM connection section.
In remote mode, the configuration path is managed by the
Agent installed on the repeater.

Detection does not modify MMDVMHost or its services.


### 1. Overview and architecture

The project includes a static Apache-served **Dashboard**, a Flask backend/API on local port `8089`, an MQTT collector, an Admin interface and an optional Telegram bot. A separate **read-only Agent** can run on the repeater and publish selected `MMDVM.ini` settings, systemd service states and system telemetry. It does not accept commands or alter MMDVMHost.

Supported layouts: Dashboard on the repeater reading a local `MMDVM.ini`; Dashboard on another machine with Agent on the repeater; MQTT broker on either host or on a third server. The legacy MMDVMHost JSON stream (`mmdvm/+/json` by default) is **separate** from Agent telemetry. The project does not install, restart or configure MMDVMHost or automatically enable its MQTT output.

### 2. Requirements and existing installations

Use Debian 12/13 or Debian-based Raspberry Pi OS with `apt`, `systemd`, root privileges and package-network access. The Dashboard needs Apache and Python 3 (dependencies are installed by the script); the Agent needs a readable `MMDVM.ini` and access to an MQTT broker. Make sure the configured MQTT port, usually `1883`, is reachable and HTTPS is available for browser access.

**A fresh machine is recommended, not mandatory.** The Dashboard installer refuses an existing `/opt/mmdvm-dashboard-mqtt`, matching service units or matching Apache API configuration; it is **not an in-place upgrade**. Existing Apache virtual hosts are preserved, but frontend files are copied into `/var/www/html/`, with confirmation and timestamped backups for conflicts, and `/api/` proxy rules are enabled globally. Inspect other websites before proceeding. Interrupted installations cannot automatically resume. The Agent installer likewise refuses existing Agent files or services rather than overwriting them, even if the files match.

### 3. Download the complete repository

```bash
apt update && apt install -y git
git clone -b main https://github.com/Mauro-go/MMDVM-Dashboard-MQTT.git /opt/MMDVM-Dashboard-MQTT
cd /opt/MMDVM-Dashboard-MQTT
```

Alternatively download and extract the **full repository** for the selected branch. A ZIP containing only the three installer scripts is insufficient: the installer also requires `backend/`, `frontend/`, `admin/`, `config/`, the Agent files and the HTTPS helper.

### 4. Automatic Dashboard installation

```bash
cd /opt/MMDVM-Dashboard-MQTT
bash install.sh
```

Choose English or Italian, then follow the prompts for: local `MMDVM.ini` versus remote MQTT Agent; local configuration path or remote Agent **Node ID and topic prefix**; local/new versus existing/remote broker, port, username, password and TLS as applicable; legacy JSON topic (local mode); HTTPS browser address; and final confirmation.

The Node ID and prefix are **user-configurable**, not fixed repeater names. `pippo` is valid. The suggested prefix is `mmdvm/<node_id>/agent/v1`, but a custom valid prefix is accepted. The Node ID is lowercased; a custom topic prefix preserves its exact case. Record the values and enter the same ones in the Agent installer.

The installer creates `mmdvm-dashboard-mqtt.service`, `mmdvm-mqtt-collector.service` and an initially disabled `mmdvm-telegram-bot.service`. It installs Apache if absent and sets up the `/api/` reverse proxy to local port `8089`. If no HTTPS virtual host exists, it can create one with a **self-signed certificate**; browsers may warn. If HTTPS already exists, the installer preserves it, but you must verify that the selected HTTPS site serves the installed frontend and proxies `/api/` correctly. Existing virtual hosts and DocumentRoot directives are not rewritten.

Expected URLs, when the HTTPS virtual host serves the frontend: `https://HOST/` (Dashboard), `https://HOST/api/admin/` (Admin) and `https://HOST/api/` (API).

### 5. Automatic Agent installation

Download the standalone **[MMDVM-MQTT-Agent.zip](MMDVM-MQTT-Agent.zip)** package directly to the repeater. Cloning the full repository is not required.

Run as root:

```bash
apt update && apt install -y wget unzip
mkdir -p /root/mmdvm-agent-installer
cd /root/mmdvm-agent-installer
wget -O MMDVM-MQTT-Agent.zip https://github.com/Mauro-go/MMDVM-Dashboard-MQTT/raw/refs/heads/main/MMDVM-MQTT-Agent.zip
unzip MMDVM-MQTT-Agent.zip
bash install.sh
```

The ZIP includes `install.sh`, `agent.py`, `mmdvm-agent.service`, `agent.json.example` and `broker.json.example`, without personal credentials or configurations.

Alternatively, clone the full repository and run `bash install.sh` from its `agent/` directory.

The prompts cover language, local/new or remote/existing broker, `MMDVM.ini` path, Node ID (default from `[General] Callsign`), exact topic prefix, publishing interval (at least five seconds), credentials and optional TLS. If requested, the installer creates a new authenticated Mosquitto broker; it preserves an existing broker. The service is named **`mmdvm-agent.service`**. It does not restart radio services. Existing Agent installations are not overwritten or upgraded automatically.

### 6. MQTT matching rules

The Dashboard and Agent must use the **same reachable broker, port, compatible credentials and identical Agent prefix**, including case. For example:

| Setting | Dashboard | Agent |
| --- | --- | --- |
| Node ID | `pippo` | `pippo` |
| Agent prefix | `mmdvm/pippo/agent/v1` | `mmdvm/pippo/agent/v1` |
| Broker | `10.0.0.100:1883` | `10.0.0.100:1883` |
| Legacy JSON topic | `mmdvm/+/json` | Separate from Agent prefix |

Agent messages use suffixes such as `/config`, `/telemetry` and `/status`. An Agent connected to its own `127.0.0.1:1883` and a Dashboard connected to a different broker **do not share messages automatically**. Use one common broker or explicitly configure an MQTT bridge. A connected MQTT client alone does not prove message delivery.

### 7. Configuration and first Admin login

| Path | Purpose |
| --- | --- |
| `/opt/mmdvm-dashboard-mqtt/config/dashboard.ini` | General settings, deployment mode, Node ID, Agent prefix |
| `/opt/mmdvm-dashboard-mqtt/data/mqtt-settings.json` | MQTT broker, credentials and legacy topic |
| `/opt/mmdvm-dashboard-mqtt/data/telegram-settings.json` | Optional Telegram settings |
| `/opt/mmdvm-dashboard-mqtt/data/events.sqlite3` | Event history (about 48-hour retention) |
| `/var/lib/mmdvm-dashboard/setup-token` | Initial Admin setup token, if still present |
| `/opt/mmdvm-agent/agent.json` | Agent Node ID, prefix, MMDVM.ini path and interval |
| `/opt/mmdvm-agent/broker.json` | Agent broker and credentials |

The automatic installer displays the initial token in its final summary when administrator setup is still pending. To retrieve it later, run `cat /var/lib/mmdvm-dashboard/setup-token` as root, then open `/api/admin/` over HTTPS to create the administrator. Keep all credentials and tokens private. Configure the optional Telegram bot through Admin before enabling its service.

### 8. Manual installation (advanced)

This is an **administrative outline**, not a separately validated installer. Back up existing services, web assets and settings first; do not apply it blindly to a running installation.

**Dashboard:** install `apache2 openssl curl python3 python3-flask python3-werkzeug python3-psutil python3-paho-mqtt` with apt. Create a system user `mmdvm-dashboard`; create `/opt/mmdvm-dashboard-mqtt/config`, `/opt/mmdvm-dashboard-mqtt/data/databases` and `/var/lib/mmdvm-dashboard` with appropriate ownership. Copy `backend/`, `admin/`, `frontend/` from the full repository. Publish the static `frontend/` files in your selected Apache DocumentRoot, after checking conflicts.

Create `config/dashboard.ini` from `config/dashboard.ini.example`; set `[deployment]` values (`mode`, `mmdvm_ini`, `node_id`, `agent_topic_prefix`) and `[paths]` appropriately. Create `/opt/mmdvm-dashboard-mqtt/data/mqtt-settings.json` (owner `mmdvm-dashboard`, mode `0600`), using the following structure and replacing placeholders:

```json
{
  "host": "10.0.0.100",
  "port": 1883,
  "topic": "mmdvm/+/json",
  "username": "MQTT_USER",
  "password": "REPLACE_ME",
  "tls": false
}
```

Prepare Admin session secrets and the initial setup token in `/var/lib/mmdvm-dashboard` as implemented in `install.sh`; protect them. Create systemd services running as `mmdvm-dashboard`, with working directory `/opt/mmdvm-dashboard-mqtt/backend` and commands:

```text
/usr/bin/python3 -u /opt/mmdvm-dashboard-mqtt/backend/mqtt_collector.py
/usr/bin/python3 -u /opt/mmdvm-dashboard-mqtt/backend/app.py
```

For the backend set `MMDVM_DASHBOARD_PORT=8089` and `MMDVM_DASHBOARD_STATE_DIR=/var/lib/mmdvm-dashboard`; allow appropriate write access to `data/`, `config/` and the state directory. Use the complete service unit definitions in `install.sh` as your reference. Telegram is optional.

Enable Apache `proxy`, `proxy_http` and `ssl`; configure a suitable HTTPS virtual host serving the frontend and add the following rules in the correct context:

```apache
ProxyPass /api/ http://127.0.0.1:8089/api/
ProxyPassReverse /api/ http://127.0.0.1:8089/api/
```

Check for `/api/` conflicts, run `apache2ctl configtest`, reload Apache, run `systemctl daemon-reload` and enable the backend and collector services.

**Agent:** install `python3 python3-paho-mqtt python3-psutil`; copy `agent/agent.py` to `/opt/mmdvm-agent/agent.py` and `agent/mmdvm-agent.service` to `/etc/systemd/system/mmdvm-agent.service`. Create `/opt/mmdvm-agent/agent.json`:

```json
{
  "node_id": "pippo",
  "topic_prefix": "mmdvm/pippo/agent/v1",
  "mmdvm_ini": "/opt/cfg/MMDVM.ini",
  "broker_settings_file": "/opt/mmdvm-agent/broker.json",
  "interval_seconds": 10
}
```

Create `/opt/mmdvm-agent/broker.json`:

```json
{
  "host": "10.0.0.100",
  "port": 1883,
  "username": "MQTT_USER",
  "password": "REPLACE_ME",
  "tls": false
}
```

Replace placeholders, set both JSON files to `root:root` and mode `0600`, then validate and enable:

```bash
python3 -B /opt/mmdvm-agent/agent.py --config /opt/mmdvm-agent/agent.json --dry-run
systemctl daemon-reload
systemctl enable --now mmdvm-agent
```

For a new broker, configure Mosquitto with authenticated listeners, suitable firewall rules and TLS where required. Do not expose unauthenticated MQTT to the Internet or alter a shared broker without checking its other clients.

### 9. Troubleshooting and maintenance

```bash
# Dashboard
systemctl status mmdvm-dashboard-mqtt mmdvm-mqtt-collector --no-pager
journalctl -u mmdvm-mqtt-collector -n 50 --no-pager
journalctl -u mmdvm-dashboard-mqtt -n 50 --no-pager
apache2ctl configtest

# Agent (on repeater)
systemctl status mmdvm-agent --no-pager
journalctl -u mmdvm-agent -n 50 --no-pager
```

If legacy radio events work but Agent telemetry does not, compare the exact `agent_topic_prefix` in `dashboard.ini` with `topic_prefix` in `agent.json`, then broker, port, credentials and any MQTT bridge. If the frontend works but API calls fail, check `/api/` proxy rules, backend port `8089` and systemd logs. The collector periodically removes events older than approximately 48 hours.

### 10. Security, backups and limitations

Never commit MQTT credentials, Telegram tokens, Admin secrets or databases to GitHub. Back up Apache, Mosquitto, application settings, web assets and service units before working on an existing machine. The scripts do not manage MMDVMHost or restart radio services. The Dashboard installer is **not idempotent and cannot resume interrupted installs**; the Agent installer refuses existing installations. Manual deployment requires local validation and administrator expertise.

---

## Repository and status

- Repository: https://github.com/Mauro-go/MMDVM-Dashboard-MQTT
- Official branch: https://github.com/Mauro-go/MMDVM-Dashboard-MQTT/tree/main
- Agent ZIP: [MMDVM-MQTT-Agent.zip](MMDVM-MQTT-Agent.zip)
- Main installer: `install.sh`
- Agent installer: `agent/install.sh`
- HTTPS helper: `scripts/setup-https.sh`

This README describes the observed installer code and its known limitations; it does not certify a production-ready end-to-end deployment.


## MQTT Agent update status

The MQTT Agent is independently versioned and reports its version through
existing MQTT telemetry. The Dashboard can expose the installed Agent
version and compare it with a package version.

The Agent distribution archive is `MMDVM-MQTT-Agent.zip`.
It contains the Agent code, systemd unit, configuration detector,
installer, version file and example configuration files.

Private configuration files and broker credentials are excluded.

**Remote Agent installation, backup and rollback are not yet implemented.
The update indication is a development feature and does not perform
remote installation.**


---

<!-- MULTI_LIVE_UPDATE_DOCUMENTATION_V1 -->

## Multi: installazione e aggiornamenti collaudati / Multi: installation and tested updates

### Italiano

**Stato:** versione `0.0.0-dev` in sviluppo. Il 7 novembre 2026 il motore di aggiornamento Multi è stato collaudato con successo su un server di test. Non è ancora certificato per distribuzioni generalizzate o aggiornamenti automatici in produzione.

#### Componenti e architettura

- **Standard:** dashboard per un singolo ripetitore, installata da `install.sh` in `/opt/mmdvm-dashboard-mqtt`; backend locale sulla porta `8089`.
- **Multi:** dashboard per più ripetitori, con configurazioni, dati e sessioni amministrative separati. L'installer `install-multi.sh` usa `/opt/mmdvm-dashboard-multi`; backend locale sulla porta `8090`.
- **Agent:** `agent/install.sh` installa il servizio `mmdvm-agent.service` sul ripetitore. Legge i dati MMDVMHost e li pubblica su un broker MQTT già disponibile; **non installa né riconfigura Mosquitto sul ripetitore**.

Il checkout di sviluppo usato per il collaudo è `/opt/MMDVM-Dashboard-MQTT-Multi`, distinto dalla destinazione dell'installer. Per la gestione dei nodi, le URL e l'isolamento dei dati consultare [MULTI.md](MULTI.md).

#### Installazione

Da una copia completa del repository, come root:

```bash
# Standard (interattivo)
bash install.sh

# Multi: verifica preventiva, senza modifiche
bash install-multi.sh --check

# Multi: installazione effettiva, solo su sistema Debian dedicato
bash install-multi.sh --install
```

**Attenzione:** non eseguire gli installer su una dashboard già funzionante per aggiornarla. Prima di installare verificare conflitti con Apache, Mosquitto e i servizi esistenti; effettuare un backup o snapshot. Il sistema Multi espone due servizi:

```bash
systemctl status mmdvm-dashboard-multi.service
systemctl status mmdvm-multi-collector.service
```

Il backend Multi risponde localmente su `http://127.0.0.1:8090/`. Per accesso remoto configurare HTTPS e reverse proxy. Non esporre direttamente il backend su Internet.

#### Aggiornamento Multi sperimentale

Il motore `scripts/update_live.py` verifica una release preparata, crea un backup, sostituisce i componenti, riavvia i servizi e controlla la salute dell'applicazione. I componenti sostituiti sono `backend/`, `frontend/`, `admin/` e `VERSION`; le directory persistenti `config/` e `data/` sono preservate.

```bash
# Eseguire dalla directory del checkout Multi
PYTHONPATH=scripts python3 -B scripts/update_live.py --check --release /percorso/release

# Applicare solo dopo snapshot/backup verificato
MMDVM_ENABLE_REAL_UPDATE=YES PYTHONPATH=scripts python3 -B scripts/update_live.py --apply --release /percorso/release

# Controllare eventuali transazioni pendenti
PYTHONPATH=scripts python3 -B scripts/update_live.py --status

# Recupero, solo dopo aver esaminato stato e log
PYTHONPATH=scripts python3 -B scripts/update_live.py --recover
```

Nell'ambiente collaudato, gli archivi e i backup sono conservati rispettivamente in `/var/lib/mmdvm-dashboard-updates/releases/` e `/var/lib/mmdvm-dashboard-updates/backups/`. La preparazione della release assegna al servizio `mmdvm-dashboard` permessi di lettura e attraversamento appropriati (directory `750`, file ordinari `640`).

**Risultati del collaudo (7 novembre 2026):** 214 test automatizzati superati. Un primo aggiornamento reale ha provocato un errore systemd `200/CHDIR` per permessi troppo restrittivi ed è stato ripristinato mediante rollback. Dopo la correzione dei permessi, un secondo aggiornamento reale è terminato con codice `0`, entrambi i servizi `active`, HTTP `200` e nessuna transazione pendente (`[]`).

Questi risultati non garantiscono il recupero da interruzioni improvvise dell'alimentazione né il funzionamento con `/opt` e `/var/lib` su filesystem differenti. L'installazione degli aggiornamenti dall'interfaccia Admin e gli aggiornamenti automatici non sono dichiarati disponibili.

#### Sicurezza e pubblicazione

Non includere nel repository password MQTT, token amministrativi, chiavi di sessione, configurazioni personali, database, backup o log privati. Prima di ogni push controllare `git status`, `git diff --cached` e i file nuovi, anche quando `.gitignore` è presente.

### English

**Status:** `0.0.0-dev`, under development. The Multi update engine passed a real deployment test on November 7, 2026; it is not certified for unattended production updates.

**Standard** monitors one repeater (`bash install.sh`, target `/opt/mmdvm-dashboard-mqtt`, local backend port `8089`). **Multi** monitors multiple repeaters with isolated configuration, data and admin sessions (`bash install-multi.sh --check` followed by `bash install-multi.sh --install` on a dedicated Debian system, target `/opt/mmdvm-dashboard-multi`, local backend port `8090`). The test checkout `/opt/MMDVM-Dashboard-MQTT-Multi` is a separate path. See [MULTI.md](MULTI.md) for architecture and node management.

The repeater-side **Agent** is installed using `agent/install.sh` and publishes telemetry to an existing reachable MQTT broker. Its installer does not install or reconfigure Mosquitto on the repeater.

The Multi services are `mmdvm-dashboard-multi.service` and `mmdvm-multi-collector.service`. Access the local backend at `http://127.0.0.1:8090/`; use HTTPS and a reverse proxy for remote access.

#### Experimental Multi updater

The updater replaces `backend/`, `frontend/`, `admin/` and `VERSION`, preserving persistent `config/` and `data/`. Run from the Multi checkout:

```bash
# Preflight (no changes)
PYTHONPATH=scripts python3 -B scripts/update_live.py --check --release /path/to/release

# Real update (requires a verified backup or snapshot)
MMDVM_ENABLE_REAL_UPDATE=YES PYTHONPATH=scripts python3 -B scripts/update_live.py --apply --release /path/to/release

# Transaction status
PYTHONPATH=scripts python3 -B scripts/update_live.py --status

# Recovery (inspect logs and status first)
PYTHONPATH=scripts python3 -B scripts/update_live.py --recover
```

In the tested environment, releases and backups are stored under `/var/lib/mmdvm-dashboard-updates/`. The staged installation prepares permissions for the `mmdvm-dashboard` service account (directories `750`, regular files `640`).

**Test evidence (November 7, 2026):** 214 automated tests passed. An initial real update failed with systemd `200/CHDIR` due to inaccessible directories and rolled back successfully. After correcting staging permissions, the second real update completed with exit code `0`, both services active, HTTP `200`, and no pending transactions.

Sudden power-loss recovery, installations using different filesystems for `/opt` and `/var/lib`, and unattended production updates require further validation. Automatic update installation from the Admin interface is not claimed to be available.

Never publish MQTT passwords, admin tokens, session secrets, private configuration, databases, backups or logs. Review staged changes before pushing to GitHub.
