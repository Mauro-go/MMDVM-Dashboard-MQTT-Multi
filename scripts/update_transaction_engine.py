#!/usr/bin/env python3
"""Motore transazionale: installazione e rollback su installazioni locali o remote."""

import hashlib
import json
import os
import shutil
from pathlib import Path

from update_transaction_state import (
    write_state,
    read_state,
    pending_transactions,
)

COMPONENTS = ("backend", "frontend", "admin", "VERSION")


def fingerprint(root):
    root = Path(root)
    result = {}

    for component in COMPONENTS:
        path = root / component

        if path.is_symlink():
            raise RuntimeError("Symlink non consentito: " + str(path))

        if component == "VERSION":
            if not path.is_file():
                raise RuntimeError("VERSION mancante")
            files = [path]
        else:
            if not path.is_dir():
                raise RuntimeError("Componente mancante: " + component)
            files = sorted(p for p in path.rglob("*") if p.is_file())

        for file in files:
            if file.is_symlink():
                raise RuntimeError("Symlink non consentito: " + str(file))
            relative = file.relative_to(root).as_posix()
            result[relative] = hashlib.sha256(file.read_bytes()).hexdigest()

    return result


def copy_snapshot(source, destination):
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)

    for component in COMPONENTS:
        src = Path(source) / component
        dst = destination / component

        if src.is_dir():
            shutil.copytree(src, dst, symlinks=True)
        else:
            shutil.copy2(src, dst)


def _remove_path(path):
    path = Path(path)
    if path.is_symlink():
        raise RuntimeError("Symlink imprevisto: " + str(path))
    if path.is_dir():
        shutil.rmtree(path)
    elif path.exists():
        path.unlink()


def _local_displaced(application, component):
    # Must live on the SAME mount as the installed component, not in
    # the persistent backup (which can be a different LXC mount).
    return Path(application) / (".mmdvm-previous-" + component)


def swap_component(staged, application, transaction, component):
    """Copy to a sibling on the destination mount, then rename locally.

    Staging may live on a different LXC bind mount even when both paths
    share /opt. Never rename directly from staging to the live component.
    """
    staged = Path(staged)
    application = Path(application)
    current = application / component
    incoming = staged / component
    previous = _local_displaced(application, component)
    local_incoming = application / (".mmdvm-incoming-" + component)

    if previous.exists() or previous.is_symlink():
        raise RuntimeError("Componente precedente non recuperato: " + str(previous))
    if local_incoming.exists() or local_incoming.is_symlink():
        raise RuntimeError("Componente temporaneo non recuperato: " + str(local_incoming))
    if not current.exists() or current.is_symlink() or incoming.is_symlink():
        raise RuntimeError("Componente non valido: " + component)
    if os.path.ismount(current):
        raise RuntimeError("Componente montato separatamente: " + str(current))

    # copytree/copy2 work across mounts. The sibling is guaranteed to be
    # created on the destination directory's filesystem.
    if incoming.is_dir():
        shutil.copytree(incoming, local_incoming, symlinks=False)
    elif incoming.is_file():
        shutil.copy2(incoming, local_incoming)
    else:
        raise RuntimeError("Componente sorgente assente: " + component)

    try:
        os.replace(current, previous)
        try:
            os.replace(local_incoming, current)
        except BaseException:
            if not current.exists() and previous.exists():
                os.replace(previous, current)
            raise
    finally:
        _remove_path(local_incoming)


def restore_snapshot(snapshot, application, transaction):
    """Ripristino ripetibile anche dopo interruzione fra due rename."""
    snapshot = Path(snapshot).resolve(strict=True)
    application = Path(application).resolve(strict=True)
    transaction = Path(transaction).resolve(strict=True)

    expected = fingerprint(snapshot)

    for component in COMPONENTS:
        source = snapshot / component
        current = application / component
        temporary = application / (".restore-" + component)
        displaced = _local_displaced(application, component)

        _remove_path(temporary)
        # An earlier failed swap can leave a previous version in the
        # application mount. The verified backup is authoritative.
        _remove_path(displaced)

        if source.is_dir():
            shutil.copytree(source, temporary, symlinks=False)
        else:
            shutil.copy2(source, temporary)

        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise RuntimeError("Symlink imprevisto: " + str(current))
            os.replace(current, displaced)

        try:
            os.replace(temporary, current)
        except BaseException:
            if displaced.exists() and not current.exists():
                os.replace(displaced, current)
            raise

        _remove_path(displaced)

    if fingerprint(application) != expected:
        raise RuntimeError("Ripristino non coerente")

def save_fingerprint(path, values):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(values, indent=2, sort_keys=True)
    )
    os.replace(temporary, path)


def verify_fingerprint(snapshot, manifest):
    expected = json.loads(Path(manifest).read_text())
    actual = fingerprint(snapshot)

    if actual != expected:
        raise RuntimeError("Integrità backup non valida")

    return True


def recover_transaction(transaction, application):
    """Recupera una transazione incompleta da backup verificato."""
    transaction = Path(transaction).resolve(strict=True)
    application = Path(application).resolve(strict=True)

    journal = transaction / "state.json"
    backup = transaction / "backup"
    manifest = transaction / "backup-fingerprint.json"

    state = read_state(journal)

    if state["status"] in ("completed", "rolled_back"):
        return state["status"]

    if state["status"] not in (
        "backup",
        "stopping",
        "installing",
        "starting",
        "rolling_back",
    ):
        raise RuntimeError("Stato non recuperabile")

    if not backup.is_dir() or not manifest.is_file():
        raise RuntimeError(
            "Backup verificabile assente: recupero bloccato"
        )

    verify_fingerprint(backup, manifest)

    state["status"] = "rolling_back"
    write_state(journal, state)

    restore_snapshot(backup, application, transaction)

    verify_fingerprint(backup, manifest)

    if fingerprint(application) != fingerprint(backup):
        raise RuntimeError("Ripristino non coerente")

    state["status"] = "rolled_back"
    write_state(journal, state)

    return "rolled_back"


def execute_test_transaction(
    release,
    application,
    backup_root,
    *,
    fail_after=None,
):
    release = Path(release).resolve(strict=True)
    application = Path(application).resolve(strict=True)
    backup_root = Path(backup_root).resolve(strict=True)

    if pending_transactions(backup_root):
        raise RuntimeError("Transazione precedente incompleta")

    if fingerprint(release) == {}:
        raise RuntimeError("Release vuota")

    transaction = backup_root / "transaction-test"

    if transaction.exists():
        raise RuntimeError("Transazione già presente")

    transaction.mkdir()
    journal = transaction / "state.json"

    backup = transaction / "backup"
    staged = transaction / "staged"

    try:
        write_state(journal, {"status": "backup"})
        copy_snapshot(application, backup)
        original = fingerprint(backup)
        save_fingerprint(
            transaction / "backup-fingerprint.json",
            original,
        )

        if fingerprint(application) != original:
            raise RuntimeError("Backup non coerente")

        copy_snapshot(release, staged)

        if fingerprint(staged) != fingerprint(release):
            raise RuntimeError("Staging non coerente")

        write_state(journal, {"status": "installing"})

        for index, component in enumerate(COMPONENTS, 1):
            swap_component(staged, application, transaction, component)

            if fail_after == index:
                raise RuntimeError("Errore simulato")

        if fingerprint(application) != fingerprint(release):
            raise RuntimeError("Installazione non coerente")

        write_state(journal, {"status": "completed"})
        return "completed"

    except Exception:
        write_state(journal, {"status": "rolling_back"})

        if backup.exists():
            verify_fingerprint(
                backup,
                transaction / "backup-fingerprint.json",
            )
            restore_snapshot(backup, application, transaction)

            if fingerprint(application) != fingerprint(backup):
                raise RuntimeError("Rollback non coerente")

            write_state(journal, {"status": "rolled_back"})

        raise
