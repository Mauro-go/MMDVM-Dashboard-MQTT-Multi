# Release v0.1.4 - MMDVM Dashboard MQTT MULTI

Release ufficiale promossa dal collaudo v0.1.3 su dash-test del 14 novembre 2026.

- Installer multi e aggiornamento delle installazioni esistenti con percorsi dinamici.
- Gestione origine aggiornamenti vincolata al repository ufficiale.
- Updater transazionale, backup e rollback; correzione EXDEV: copia per-component nel filesystem di destinazione prima di os.replace().
- Admin, MQTT, D-Star, filtri e interfaccia derivati dal progetto MULTI TEST.

La release non e' ancora stata convalidata sulla macchina di produzione 192.168.0.128.
Prima del deploy proteggere dati, config, dashboard.ini, credenziali e servizi.
