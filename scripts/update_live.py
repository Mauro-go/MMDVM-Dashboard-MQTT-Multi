#!/usr/bin/env python3
"""Aggiornamento Multi con backup persistente e recupero systemd."""

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import uuid
from pathlib import Path

from update_install import (
    BASE, DESTINATION, SERVICES, COMPONENTS,
    HEALTH_URL, verify_release, health, run_systemctl,
)
from update_production_guard import production_lock, verify_services
from update_transaction_engine import (
    fingerprint, copy_snapshot, save_fingerprint,
    verify_fingerprint, swap_component, restore_snapshot,
)
from update_transaction_state import (
    write_state, read_state, pending_transactions,
)

BACKUPS = BASE / "backups"


def record(journal, state, status):
    state["status"] = status
    write_state(journal, state)


def stop_services():
    for service in reversed(SERVICES):
        run_systemctl("stop", service)


def start_services():
    for service in SERVICES:
        run_systemctl("start", service)


def check_release(release):
    release = verify_release(Path(release))

    manifest = release / "MANIFEST.sha256"
    expected = {}

    for line in manifest.read_text().splitlines():
        if len(line) < 67 or line[64:66] != "  ":
            raise RuntimeError("Formato manifest non valido")

        digest = line[:64]
        name = line[66:]

        if len(digest) != 64 or any(
            c not in "0123456789abcdefABCDEF" for c in digest
        ):
            raise RuntimeError("SHA-256 non valido")

        relative = Path(name)

        if (
            relative.is_absolute()
            or ".." in relative.parts
            or not relative.parts
            or relative.parts[0] not in COMPONENTS
            or name in expected
        ):
            raise RuntimeError("Percorso manifest non valido")

        expected[name] = digest.lower()

    actual = fingerprint(release)

    if actual != expected:
        raise RuntimeError(
            "Manifest incompleto o contenuto release differente"
        )

    for root, directories, files in os.walk(
        release, followlinks=False
    ):
        for name in directories + files:
            path = Path(root) / name
            if path.is_symlink():
                raise RuntimeError(
                    "Symlink non consentito: " + str(path)
                )

    return release


def check_destination():
    destination = DESTINATION.resolve(strict=True)

    if destination != DESTINATION:
        raise RuntimeError("Destinazione non autorizzata")

    for component in COMPONENTS:
        path = destination / component
        if path.is_symlink() or not path.exists():
            raise RuntimeError(
                "Componente installato non valido: " + component
            )

    return destination


def transaction_paths(transaction):
    return (
        transaction / "state.json",
        transaction / "backup",
        transaction / "backup-fingerprint.json",
    )


def cleanup_staging(state):
    name = state.get("staging")

    if not name:
        return

    path = Path(name)
    parent = DESTINATION.parent.resolve(strict=True)

    if (
        path.parent != parent
        or not path.name.startswith(".mmdvm-stage-")
        or path.is_symlink()
    ):
        raise RuntimeError("Percorso staging non autorizzato")

    if path.is_dir():
        shutil.rmtree(path)


def restore_and_restart(transaction, state):
    journal, backup, manifest = transaction_paths(transaction)

    if state.get("destination") != str(DESTINATION):
        raise RuntimeError("Destinazione journal non valida")

    # Il backup viene verificato PRIMA di fermare i servizi.
    verify_fingerprint(backup, manifest)

    record(journal, state, "rolling_back")

    stop_services()

    restore_snapshot(backup, DESTINATION, transaction)

    verify_fingerprint(backup, manifest)

    if fingerprint(DESTINATION) != fingerprint(backup):
        raise RuntimeError("Ripristino file non coerente")

    start_services()

    if not health(timeout=45):
        raise RuntimeError(
            "File ripristinati ma servizi non sani; "
            "transazione ancora aperta"
        )

    cleanup_staging(state)

    record(journal, state, "rolled_back")

    print("ROLLBACK COMPLETATO:", transaction)


def recover_pending():
    pending = pending_transactions(BACKUPS)

    if not pending:
        print("Nessuna transazione da recuperare")
        return

    if len(pending) != 1:
        raise RuntimeError(
            "Più transazioni incomplete: recupero manuale necessario"
        )

    transaction = Path(pending[0]["directory"]).resolve(strict=True)

    if transaction.parent != BACKUPS.resolve(strict=True):
        raise RuntimeError("Transazione fuori directory autorizzata")

    journal, backup, manifest = transaction_paths(transaction)
    state = read_state(journal)

    if state.get("destination") != str(DESTINATION):
        raise RuntimeError("Destinazione journal non valida")

    if state["status"] == "preparing":
        # In preparing nessun servizio è stato fermato e
        # nessun componente installato è stato modificato.
        if not health(timeout=10):
            raise RuntimeError(
                "Preparazione interrotta, ma servizi non sani"
            )

        cleanup_staging(state)
        record(journal, state, "rolled_back")
        print("Preparazione incompleta annullata")
        return

    restore_and_restart(transaction, state)



def prepare_staged_permissions(staged):
    """Keep executable code root-owned and readable by the Multi service user."""
    import stat

    staged = Path(staged)
    for component in COMPONENTS:
        root = staged / component
        if root.is_symlink():
            raise RuntimeError("Symlink non consentito: " + str(root))
        if root.is_dir():
            for directory, dirs, files in os.walk(root, followlinks=False):
                directory = Path(directory)
                if directory.is_symlink():
                    raise RuntimeError("Symlink non consentito")
                os.chown(directory, 0, 0)
                os.chmod(directory, 0o755)
                for name in dirs:
                    if (directory / name).is_symlink():
                        raise RuntimeError("Symlink non consentito")
                for name in files:
                    child = directory / name
                    if child.is_symlink() or not child.is_file():
                        raise RuntimeError("File non valido: " + str(child))
                    executable = bool(stat.S_IMODE(child.stat().st_mode) & 0o111)
                    os.chown(child, 0, 0)
                    os.chmod(child, 0o755 if executable else 0o644)
        elif root.is_file():
            os.chown(root, 0, 0)
            os.chmod(root, 0o644)
        else:
            raise RuntimeError("Componente mancante: " + str(root))


def preserve_local_frontend(destination, staging):
    """Preserve local images and favicons absent from the official release.

    Reject conflicting files rather than silently overwriting a new release.
    """
    source = destination / "frontend"
    target = staging / "frontend"
    allowed = ("images/",)
    favicons = {
        "favicon.ico", "favicon-16x16.png", "favicon-32x32.png",
        "apple-touch-icon.png", "android-chrome-192x192.png",
        "android-chrome-512x512.png",
    }
    for file in source.rglob("*"):
        if file.is_symlink():
            raise RuntimeError("Link simbolico nel frontend installato")
        if not file.is_file():
            continue
        relative = file.relative_to(source)
        name = relative.as_posix()
        if name not in favicons and not any(name.startswith(x) for x in allowed):
            continue
        output = target / relative
        if output.exists():
            # Release content wins when the same path exists; do not replace it.
            continue
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file, output, follow_symlinks=False)


def install(release):
    release = check_release(release)
    destination = check_destination()

    if pending_transactions(BACKUPS):
        raise RuntimeError(
            "Transazione incompleta: eseguire prima --recover"
        )

    verify_services(destination)

    if not health(timeout=15):
        raise RuntimeError("Servizi non sani prima dell'aggiornamento")

    BACKUPS.mkdir(parents=True, exist_ok=True, mode=0o700)

    transaction = BACKUPS / (
        time.strftime("%Y%m%d-%H%M%S")
        + "-" + uuid.uuid4().hex[:8]
    )

    transaction.mkdir(mode=0o700)

    staging = destination.parent / (
        ".mmdvm-stage-" + transaction.name
    )

    journal, backup, manifest = transaction_paths(transaction)

    state = {
        "status": "preparing",
        "destination": str(destination),
        "release": str(release),
        "staging": str(staging),
        "services": list(SERVICES),
    }

    write_state(journal, state)

    services_may_be_stopped = False

    try:
        print("Preparazione backup:", backup)

        before = fingerprint(destination)

        copy_snapshot(destination, backup)
        save_fingerprint(manifest, fingerprint(backup))

        verify_fingerprint(backup, manifest)

        if fingerprint(backup) != before:
            raise RuntimeError("Backup diverso dall'originale")

        print("Preparazione nuova release:", staging)

        copy_snapshot(release, staging)

        if fingerprint(staging) != fingerprint(release):
            raise RuntimeError("Staging non coerente")

        preserve_local_frontend(destination, staging)
        expected_installed = fingerprint(staging)

        # Preflight: cross-filesystem staging is supported by copying each
        # component to a sibling inside the installation before rename.
        for component in COMPONENTS:
            current = destination / component
            incoming = staging / component
            if current.is_symlink() or incoming.is_symlink() or not current.exists() or not incoming.exists():
                raise RuntimeError("Componente non valido: " + component)
            if os.path.ismount(current):
                raise RuntimeError("Componente montato separatamente: " + str(current))
            for prefix in (".mmdvm-previous-", ".mmdvm-incoming-"):
                residue = destination / (prefix + component)
                if residue.exists() or residue.is_symlink():
                    raise RuntimeError("Residuo di aggiornamento: " + str(residue))

        # Persistenza dei file prima di toccare i servizi.
        os.sync()

        record(journal, state, "backup")
        record(journal, state, "stopping")

        services_may_be_stopped = True

        print("Arresto servizi")
        prepare_staged_permissions(staging)
        stop_services()

        record(journal, state, "installing")

        print("Installazione componenti")
        for component in COMPONENTS:
            swap_component(
                staging,
                destination,
                transaction,
                component,
            )

        if fingerprint(destination) != expected_installed:
            raise RuntimeError("Installazione non coerente")

        record(journal, state, "starting")

        print("Riavvio servizi")
        start_services()

        if not health(timeout=45):
            raise RuntimeError("Controllo HTTP/systemd fallito")

        cleanup_staging(state)

        # The verified backup is retained in the transaction directory;
        # old live components are no longer needed after a healthy start.
        for component in COMPONENTS:
            previous = destination / (".mmdvm-previous-" + component)
            if previous.is_symlink():
                raise RuntimeError("Residuo symlink imprevisto: " + str(previous))
            if previous.is_dir():
                shutil.rmtree(previous)
            elif previous.exists():
                previous.unlink()

        record(journal, state, "completed")

        print("AGGIORNAMENTO COMPLETATO")
        print("Backup conservato:", backup)

    except Exception as error:
        print("ERRORE:", error, file=sys.stderr)

        if services_may_be_stopped:
            try:
                restore_and_restart(transaction, state)
            except Exception as recovery_error:
                print(
                    "RECUPERO NON COMPLETATO:",
                    recovery_error,
                    file=sys.stderr,
                )
                print(
                    "Journal conservato:", journal,
                    file=sys.stderr,
                )
        else:
            # Nessun componente installato è stato toccato.
            # Conserviamo lo stato preparing per --recover.
            print(
                "Preparazione interrotta; usare --recover",
                file=sys.stderr,
            )

        raise


def main():
    parser = argparse.ArgumentParser()

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--recover", action="store_true")
    mode.add_argument("--status", action="store_true")

    parser.add_argument("--release")

    args = parser.parse_args()

    if os.geteuid() != 0:
        parser.error("Eseguire come root")

    if args.status:
        pending = pending_transactions(BACKUPS)
        print(json.dumps(pending, indent=2))
        return

    with production_lock():
        check_destination()

        if args.recover:
            recover_pending()
            return

        if not args.release:
            parser.error("--release obbligatorio")

        release = check_release(args.release)

        if args.check:
            if pending_transactions(BACKUPS):
                raise RuntimeError("Transazioni incomplete presenti")

            verify_services()
            if not health(timeout=15):
                raise RuntimeError("HTTP/systemd non sani")

            print("PREFLIGHT OK")
            print("Release:", release)
            print("Destinazione:", DESTINATION)
            print("Servizi:", ", ".join(SERVICES))
            print("Nessuna modifica effettuata")
            return

        if args.apply:
            if os.environ.get("MMDVM_ENABLE_REAL_UPDATE") != "YES":
                raise RuntimeError(
                    "Aggiornamento reale non autorizzato"
                )

            install(release)


if __name__ == "__main__":
    main()
