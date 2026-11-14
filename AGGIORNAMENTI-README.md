# Sistema aggiornamenti Dashboard MULTI

## Architettura

- Admin autenticato: controllo release, selezione modalità e richiesta esplicita di installazione.
- Il backend non esegue comandi privilegiati. Scrive soltanto `data/update-request.json` con un tag semver verificato.
- Il servizio root `mmdvm-multi-update.service`, avviato da timer ogni circa 10 minuti, interroga GitHub, verifica la release stabile, scarica l'archivio e prepara un manifest SHA-256.
- Il motore transazionale effettua backup persistente, staging, arresto e riavvio dei tre servizi, controlli HTTP/systemd e rollback in caso di errore.
- Modalità iniziale `manual`. In modalità `automatic` il worker installa release più recenti quando la versione installata è semver valida.
- I dati e le configurazioni sono esterni ai componenti aggiornati. Le immagini e le favicon personalizzate del frontend vengono mantenute.

## Installazione nuova

`bash install-multi.sh --check` e, su Debian **dedicato pulito**, `bash install-multi.sh --install`.

## Installazione già esistente

**Non eseguire `install-multi.sh` su una dashboard esistente.** L'installer non è un programma di migrazione.

La procedura `install-updater-existing.sh` installa soltanto gli script root e il timer, senza sostituire codice né riavviare dashboard/collector/Telegram. Richiede che la dashboard abbia già `VERSION` e che il backend Admin con i nuovi endpoint sia stato distribuito separatamente con un piano di aggiornamento controllato. **Non installarla da sola aspettandosi che l'Admin venga aggiornato.**

Non creare un numero di versione fittizio per una installazione esistente. Prima identificare un commit/release corrispondente al codice realmente installato e fare un backup.

## Limitazioni della verifica

Test unitari simulano l'installazione, il rollback e le interruzioni. Non sostituiscono un collaudo sul Debian reale con systemd, HTTP, MQTT, Telegram e una release GitHub effettiva. Prima di attivare l'automatico, eseguire un aggiornamento manuale completo su un ambiente di prova.

Non installare una release che non contenga `VERSION` semver, i tre componenti applicativi e tutti i file richiesti dal validatore.
