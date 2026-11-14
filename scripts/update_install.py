#!/usr/bin/env python3
"""Installatore transazionale persistente MMDVM Dashboard Multi."""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import time
import urllib.request
from update_transaction_state import pending_transactions, read_state
from datetime import datetime, timezone
from pathlib import Path

COMPONENTS = ("backend", "frontend", "admin", "VERSION")
from update_runtime import app_root, base, metadata
BASE = base()
DESTINATION = app_root()
SERVICES = tuple(metadata()["services"])
HEALTH_URL = "http://127.0.0.1:%d/" % metadata()["port"]


def run_systemctl(action, service):
    subprocess.run(
        ["systemctl", action, service],
        check=True,
        timeout=45,
    )


def health(timeout=30):
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        services_ok = all(
            subprocess.run(
                ["systemctl", "is-active", "--quiet", service],
                check=False,
            ).returncode == 0
            for service in SERVICES
        )

        if services_ok:
            try:
                with urllib.request.urlopen(
                    HEALTH_URL, timeout=3
                ) as response:
                    if response.status == 200:
                        return True
            except Exception:
                pass

        time.sleep(1)

    return False


def verify_release(release):
    release = release.resolve(strict=True)

    if BASE.resolve() not in release.parents:
        raise RuntimeError("Release fuori dalla directory autorizzata")

    if not release.is_dir():
        raise RuntimeError("Release non valida")

    for component in COMPONENTS:
        path = release / component

        if path.is_symlink():
            raise RuntimeError("Symlink non consentito")

        if component == "VERSION":
            if not path.is_file():
                raise RuntimeError("VERSION mancante")
        elif not path.is_dir():
            raise RuntimeError("Componente mancante: " + component)

    for path in release.rglob("*"):
        if path.is_symlink():
            raise RuntimeError("Symlink interno non consentito")

    manifest = release / "MANIFEST.sha256"

    if not manifest.is_file():
        raise RuntimeError("Manifest SHA-256 mancante")

    subprocess.run(
        ["sha256sum", "--check", "--status", str(manifest)],
        cwd=release,
        check=True,
    )

    return release


def copy_components(source, destination):
    """Copia componenti; utilizzabile solo per staging/backup vuoti."""
    destination.mkdir(parents=True, exist_ok=True)

    for component in COMPONENTS:
        src = source / component
        dst = destination / component

        if dst.exists() or dst.is_symlink():
            raise RuntimeError(
                "Destinazione componente già presente: " + str(dst)
            )

        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)


def stage_components(source, destination):
    """Prepara tutti i componenti senza modificare quelli installati."""
    destination.mkdir(parents=True, exist_ok=False)
    copy_components(source, destination)


def replace_components(source, destination, transaction):
    """Sostituisce ogni componente mediante rename sullo stesso filesystem.

    La sostituzione dell'intero insieme non è atomica:
    il journal consente di individuare transazioni incomplete.
    """
    transaction = Path(transaction)
    old = transaction / "previous"
    old.mkdir(exist_ok=True)

    for component in COMPONENTS:
        current = destination / component
        replacement = source / component
        previous = old / component

        if not replacement.exists():
            raise RuntimeError(
                "Componente preparato mancante: " + component
            )

        if current.exists():
            os.replace(current, previous)

        os.replace(replacement, current)


def recover_components(backup, destination):
    """Ripristino ripetibile da backup persistente.

    Prepara una copia completa prima di modificare l'installazione.
    I componenti vengono scambiati mediante rename.
    """
    backup = Path(backup).resolve(strict=True)
    destination = Path(destination).resolve(strict=True)

    for component in COMPONENTS:
        source = backup / component

        if source.is_symlink():
            raise RuntimeError("Symlink nel backup: " + component)

        valid = (
            source.is_file()
            if component == "VERSION"
            else source.is_dir()
        )

        if not valid:
            raise RuntimeError("Backup incompleto: " + component)

        if source.is_dir():
            if any(p.is_symlink() for p in source.rglob("*")):
                raise RuntimeError(
                    "Symlink interno nel backup: " + component
                )

    for component in COMPONENTS:
        source = backup / component
        current = destination / component
        temporary = destination / (
            ".mmdvm-restore-" + component
        )
        previous = destination / (
            ".mmdvm-previous-" + component
        )

        # Un tentativo precedente può aver lasciato questi file.
        # Sono residui della transazione di recupero, non il backup.
        for leftover in (temporary, previous):
            if leftover.is_symlink():
                raise RuntimeError(
                    "Symlink imprevisto: " + str(leftover)
                )
            if leftover.is_dir():
                shutil.rmtree(leftover)
            elif leftover.exists():
                leftover.unlink()

        if source.is_dir():
            shutil.copytree(source, temporary)
        else:
            shutil.copy2(source, temporary)

        if current.exists() or current.is_symlink():
            os.replace(current, previous)

        try:
            os.replace(temporary, current)
        except Exception:
            if previous.exists() and not current.exists():
                os.replace(previous, current)
            raise

        if previous.is_dir():
            shutil.rmtree(previous)
        elif previous.exists():
            previous.unlink()

def save_state(path, state):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    os.replace(tmp, path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--release")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--recover", action="store_true")
    args = parser.parse_args()

    if args.recover:
        from update_production_guard import (
            production_lock,
        )
        from update_transaction_engine import (
            recover_transaction,
        )

        if os.geteuid() != 0:
            raise RuntimeError("Recupero riservato a root")

        with production_lock():
            pending = pending_transactions(BASE / "backups")

            if not pending:
                print("Nessuna transazione da recuperare")
                return

            if len(pending) != 1:
                raise RuntimeError(
                    "Più transazioni incomplete: "
                    "intervento manuale necessario"
                )

            destination = DESTINATION.resolve(strict=True)

            for service in SERVICES:
                active = subprocess.run(
                    ["systemctl", "is-active", "--quiet", service],
                    check=False,
                )
                if active.returncode == 0:
                    raise RuntimeError(
                        "Recupero bloccato: servizio attivo "
                        + service
                    )

            transaction = Path(pending[0]["directory"])

            state = read_state(
                transaction / "state.json"
            )

            if state.get("destination") != str(destination):
                raise RuntimeError(
                    "Destinazione journal non corrispondente"
                )

            result = recover_transaction(
                transaction,
                destination,
            )

            print("Recupero:", result)
        return

    if args.status:
        pending = pending_transactions(BASE / "backups")
        if pending:
            print(json.dumps(pending, indent=2))
        else:
            print("Nessuna transazione incompleta")
        return

    if args.dry_run == args.apply:
        parser.error("Specificare --dry-run oppure --apply")

    if not args.release:
        parser.error("--release è obbligatorio con --dry-run e --apply")

    release = verify_release(Path(args.release))
    destination = DESTINATION.resolve(strict=True)

    if not destination.is_dir():
        raise RuntimeError("Installazione Multi non trovata")

    for component in COMPONENTS:
        if not (destination / component).exists():
            raise RuntimeError("Componente installato mancante")

    print("Release:", release)
    print("Destinazione:", destination)
    print("Servizi:", ", ".join(SERVICES))

    if args.dry_run:
        print("DRY-RUN OK: nessuna modifica effettuata")
        return


    from update_production_guard import (
        production_lock,
        production_preflight,
    )
    from update_transaction_engine import (
        fingerprint,
        copy_snapshot,
        save_fingerprint,
        verify_fingerprint,
        swap_component,
        recover_transaction,
    )
    from update_transaction_state import write_state

    with production_lock():
        production_preflight(release, destination)

        if not health():
            raise RuntimeError(
                "Controllo iniziale HTTP/systemd fallito"
            )

        if os.environ.get("MMDVM_ENABLE_REAL_UPDATE") != "YES":
            raise RuntimeError(
                "Installatore reale non ancora autorizzato: "
                "MMDVM_ENABLE_REAL_UPDATE=YES richiesto"
            )

        backups = BASE / "backups"
        backups.mkdir(parents=True, exist_ok=True)

        transaction = backups / (
            datetime.now(timezone.utc).strftime(
                "%Y%m%d-%H%M%S"
            ) + "-" + str(os.getpid())
        )

        transaction.mkdir(mode=0o700)

        journal = transaction / "state.json"
        backup = transaction / "backup"
        staged = transaction / "staged"
        manifest = transaction / "backup-fingerprint.json"

        state = {
            "status": "preparing",
            "destination": str(destination),
            "release": str(release),
            "services": list(SERVICES),
        }

        write_state(journal, state)

        services_touched = False

        try:
            copy_snapshot(destination, backup)
            original = fingerprint(backup)
            save_fingerprint(manifest, original)

            if fingerprint(destination) != original:
                raise RuntimeError("Backup non coerente")

            copy_snapshot(release, staged)

            if fingerprint(staged) != fingerprint(release):
                raise RuntimeError("Staging non coerente")

            verify_fingerprint(backup, manifest)

            state["status"] = "backup"
            write_state(journal, state)

            state["status"] = "stopping"
            write_state(journal, state)
            services_touched = True

            for service in reversed(SERVICES):
                run_systemctl("stop", service)

            state["status"] = "installing"
            write_state(journal, state)

            for component in COMPONENTS:
                swap_component(
                    staged,
                    destination,
                    transaction,
                    component,
                )

            if fingerprint(destination) != fingerprint(release):
                raise RuntimeError("Installazione non coerente")

            state["status"] = "starting"
            write_state(journal, state)

            for service in SERVICES:
                run_systemctl("start", service)

            if not health():
                raise RuntimeError(
                    "Controllo HTTP/systemd dopo aggiornamento fallito"
                )

            state["status"] = "completed"
            write_state(journal, state)

            print("AGGIORNAMENTO COMPLETATO")
            print("Backup:", backup)

        except Exception:
            if services_touched:
                state["status"] = "rolling_back"
                write_state(journal, state)

                for service in reversed(SERVICES):
                    subprocess.run(
                        ["systemctl", "stop", service],
                        check=False,
                    )

                verify_fingerprint(backup, manifest)
                recover_transaction(
                    transaction,
                    destination,
                )

                # Il recupero dei file è completato, ma la
                # transazione resta aperta fino alla verifica
                # dei servizi.
                state["status"] = "rolling_back"
                write_state(journal, state)

                for service in SERVICES:
                    run_systemctl("start", service)

                if not health():
                    raise RuntimeError(
                        "File ripristinati, ma servizi non sani: "
                        "transazione ancora aperta"
                    )

                state["status"] = "rolled_back"
                write_state(journal, state)

            raise


if __name__ == "__main__":
    main()
