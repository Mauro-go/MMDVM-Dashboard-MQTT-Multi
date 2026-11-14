#!/usr/bin/env python3
"""Controlli obbligatori prima di un aggiornamento reale Multi."""

import fcntl
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path

from update_transaction_state import pending_transactions

from update_runtime import app_root, base, metadata
BASE = base()
DESTINATION = app_root()
SERVICES = tuple(metadata()["services"])


@contextmanager
def production_lock(base=BASE):
    base = Path(base)
    base.mkdir(parents=True, exist_ok=True)

    lockfile = base / "install.lock"

    with lockfile.open("a+") as handle:
        try:
            fcntl.flock(
                handle,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            raise RuntimeError(
                "Un altro aggiornamento è già in corso"
            )

        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def verify_services(destination=DESTINATION):
    destination = Path(destination).resolve(strict=True)

    for service in SERVICES:
        result = subprocess.run(
            [
                "systemctl",
                "show",
                service,
                "--property=LoadState",
                "--property=WorkingDirectory",
                "--property=ExecStart",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )

        values = dict(
            line.split("=", 1)
            for line in result.stdout.splitlines()
            if "=" in line
        )

        if values.get("LoadState") != "loaded":
            raise RuntimeError(
                "Servizio non caricato: " + service
            )

        working = values.get("WorkingDirectory", "")
        command = values.get("ExecStart", "")

        if (
            str(destination) not in working
            and str(destination) not in command
        ):
            raise RuntimeError(
                "Servizio non associato alla directory Multi: "
                + service
            )

        active = subprocess.run(
            ["systemctl", "is-active", "--quiet", service],
            check=False,
            timeout=10,
        )

        if active.returncode != 0:
            raise RuntimeError(
                "Servizio non attivo: " + service
            )

    return True


def verify_no_pending(base=BASE):
    pending = pending_transactions(
        Path(base) / "backups"
    )

    if pending:
        raise RuntimeError(
            "Transazioni incomplete presenti: "
            + ", ".join(
                item["directory"] for item in pending
            )
        )

    return True


def production_preflight(release, destination=DESTINATION):
    from update_install import verify_release

    if os.geteuid() != 0:
        raise RuntimeError("Sono richiesti privilegi root")

    release = verify_release(Path(release))
    destination = Path(destination).resolve(strict=True)

    if destination != DESTINATION.resolve(strict=True):
        raise RuntimeError(
            "Destinazione diversa da quella installata"
        )

    verify_no_pending()
    verify_services(destination)

    return {
        "release": str(release),
        "destination": str(destination),
        "services": list(SERVICES),
        "ready": True,
    }
