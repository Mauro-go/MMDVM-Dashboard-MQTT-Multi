# MMDVM Dashboard MQTT MULTI — Aggiornamenti / Updates

## Italiano

### 1. Novità della versione ufficiale v0.1.4

La v0.1.4 ufficiale integra il lavoro verificato nell'ambiente MULTI TEST (v0.1.3), inclusi:

- Sistema di aggiornamento dalla sezione Admin autenticata, con controllo delle release stabili GitHub e modalità iniziale **manuale**; la modalità **automatica** è opzionale.
- Worker privilegiato separato dal backend web: l'Admin registra una richiesta di aggiornamento, mentre il servizio systemd dedicato la elabora.
- Aggiornamento transazionale con controlli preliminari, backup persistente, preparazione dei componenti, riavvio dei servizi, verifica HTTP/systemd e rollback in caso di errore.
- Correzione dell'errore **EXDEV** (`Invalid cross-device link`): i componenti vengono preparati sul filesystem di destinazione prima della sostituzione atomica.
- Gestione esplicita dell'origine degli aggiornamenti, vincolata al repository ufficiale `Mauro-go/MMDVM-Dashboard-MQTT-Multi`.
- Migliorie MULTI a installer, interfaccia Admin, backend, raccolta MQTT, gestione D-Star e componenti frontend.
- Conservazione dei dati e delle configurazioni esterne ai componenti aggiornati, incluse immagini/favicon personalizzate gestite dal sistema.

**Collaudo:** sette test mirati dell'updater sono stati superati. Su `dash-test` è stato completato un aggiornamento di prova alla v0.1.3 con servizio attivo e risposta HTTP 200. La v0.1.4 **non è ancora stata collaudata sulla macchina di produzione**.

### 2. Nuova installazione

Su un sistema Debian dedicato e pulito, verificare prima i requisiti:

```bash
bash install-multi.sh --check
```

Procedere con una nuova installazione soltanto dopo aver esaminato la configurazione:

```bash
bash install-multi.sh --install
```

**Non eseguire `install-multi.sh` su una dashboard già installata:** non è uno strumento di migrazione.

### 3. Upgrade di una versione precedente senza updater automatico

Questa è una **procedura di migrazione controllata**, non un singolo comando universale. I percorsi e i servizi possono cambiare tra installazioni.

1. **Rilevare lo stato attuale**, senza modificarlo: percorso dell'applicazione, versione o commit effettivo, servizi systemd, porta HTTP, configurazione Apache/HTTPS, broker e nodi MQTT, integrazione Telegram e modalità MULTI. Non inventare una versione se il file `VERSION` è assente.
2. **Eseguire un backup esterno all'albero applicativo** di `config/`, `data/`, `dashboard.ini`, credenziali, eventuali immagini personalizzate, unità systemd e configurazione Apache. Verificare che il backup sia leggibile e predisporre un piano di ripristino.
3. **Preparare e confrontare** i sorgenti ufficiali v0.1.4 con i file installati. Identificare personalizzazioni, percorsi effettivi e dipendenze; verificare che la release contenga tutti i componenti richiesti. Non copiare indiscriminatamente `config/` o `data/` dalla release.
4. **Migrare il codice applicativo in modo controllato** (backend, frontend e Admin), preservando configurazioni e dati. Questa fase richiede un piano specifico per l'installazione esistente e una finestra di manutenzione; lo script `install-updater-existing.sh` **non la esegue**.
5. **Verificare il funzionamento**: servizi attivi, HTTP 200, accesso Admin, nodi e dati MQTT, funzioni MULTI e Telegram se presente. In caso di errore, ripristinare il backup prima di procedere.
6. **Configurare l'origine ufficiale** in `config/update-repository` con il valore `Mauro-go/MMDVM-Dashboard-MQTT-Multi`, solo dopo aver verificato la compatibilità della versione installata e dei nuovi endpoint Admin. Non utilizzare il repository TEST.
7. **Installare soltanto ora l'infrastruttura updater** con lo script fornito nella stessa release ufficiale, specificando percorso reale, porta e unità systemd realmente associate all'installazione:

   ```bash
   bash install-updater-existing.sh /PERCORSO/REALE/APP PORTA servizio-dashboard.service [altri-servizi.service]
   ```

   I valori in maiuscolo e i nomi dei servizi sono **segnaposto**, non un comando da copiare senza adattamento. Lo script controlla struttura, `VERSION`, origine, servizi e runtime, chiede `CONFERMO` e abilita un timer systemd. Se un updater è già presente, interrompe l'operazione per evitare sovrascritture.
8. **Lasciare la modalità manuale** e verificare la ricerca delle release e il flusso di richiesta/aggiornamento. Abilitare l'automatico soltanto dopo un aggiornamento manuale completo e un collaudo con rollback.

**Avvertenza:** non basta installare il timer per aggiornare una vecchia dashboard. L'Admin deve già includere gli endpoint nuovi e il codice applicativo deve essere stato migrato. Non eliminare backup o installazioni di prova prima della verifica finale.

---

## English

### 1. What's new in official v0.1.4

Official v0.1.4 incorporates the work validated in the MULTI TEST environment (v0.1.3), including:

- An authenticated Admin update interface for checking stable GitHub releases; **manual** is the initial update mode and **automatic** is optional.
- A privileged updater worker separate from the web backend: Admin records an update request, and a dedicated systemd service processes it.
- Transactional updates with preflight checks, persistent backups, component staging, service restarts, HTTP/systemd health checks, and rollback on failure.
- A fix for the **EXDEV** (`Invalid cross-device link`) failure: components are prepared on the destination filesystem before atomic replacement.
- Explicit update-origin validation, restricted to the official `Mauro-go/MMDVM-Dashboard-MQTT-Multi` repository.
- MULTI improvements to the installer, Admin UI, backend, MQTT collection, D-Star handling, and frontend components.
- Preservation of configuration and data stored outside updated components, including custom frontend images/favicons handled by the updater.

**Validation:** seven focused updater tests passed. A test update to v0.1.3 completed on `dash-test`, with the service active and HTTP 200. v0.1.4 **has not yet been validated on the production machine**.

### 2. Fresh installation

On a clean, dedicated Debian system, check requirements first:

```bash
bash install-multi.sh --check
```

Only after reviewing the configuration, perform a fresh installation:

```bash
bash install-multi.sh --install
```

**Do not run `install-multi.sh` on an existing dashboard:** it is not a migration utility.

### 3. Upgrading an older installation without automatic updates

This is a **controlled migration workflow**, not a universal one-line command. Paths and service names vary by deployment.

1. **Inventory the existing installation** without changing it: application path, actual version or commit, systemd services, HTTP port, Apache/HTTPS configuration, MQTT broker and nodes, Telegram integration, and MULTI mode. Never assign a made-up version if `VERSION` is missing.
2. **Create a backup outside the application tree** covering `config/`, `data/`, `dashboard.ini`, credentials, custom images, systemd units, and Apache configuration. Verify that the backup is readable and plan recovery.
3. **Stage and compare** official v0.1.4 sources against installed files. Identify customizations, actual paths, and dependencies; verify the release includes all required components. Do not blindly overwrite `config/` or `data/` with release files.
4. **Migrate application code in a controlled maintenance window** (backend, frontend, and Admin), preserving configuration and data. This step requires a deployment-specific plan; `install-updater-existing.sh` **does not perform it**.
5. **Check application health**: active services, HTTP 200, Admin access, MQTT nodes/data, MULTI functions, and Telegram where enabled. Restore the backup if validation fails.
6. **Set the official update origin** in `config/update-repository` to `Mauro-go/MMDVM-Dashboard-MQTT-Multi`, only after confirming installed-version compatibility and the new Admin endpoints. Do not use the TEST repository.
7. **Only then install the updater infrastructure** from the same official release, using the actual application path, HTTP port, and systemd units associated with that installation:

   ```bash
   bash install-updater-existing.sh /ACTUAL/APP/PATH PORT dashboard-service.service [other-services.service]
   ```

   Uppercase values and service names are **placeholders**, not a command to run unchanged. The script checks the installation structure, `VERSION`, origin, services, and runtime; prompts for `CONFERMO`; and enables a systemd timer. It refuses to overwrite an existing updater.
8. **Keep manual mode enabled** and test release discovery and the update-request workflow. Enable automatic updates only after a full manual upgrade and rollback validation.

**Warning:** installing the timer alone will not upgrade an old dashboard. The Admin must already contain the new endpoints, and the application code must have been migrated. Retain backups and test installations until final verification.
