#!/usr/bin/env python3
"""Root-only update worker. Web process can request a version, never run root commands."""
import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from update_release import extract_release, archive_url
from update_origin import get_repository
from update_transaction_engine import fingerprint
from update_live import install, recover_pending, BACKUPS
from update_transaction_state import pending_transactions
from update_production_guard import production_lock

from update_runtime import app_root, base
DATA = app_root() / "data"
BASE = base()
REQUEST = DATA / "update-request.json"
SETTINGS = DATA / "update-settings.json"
RESULT = DATA / "update-result.json"
VERSION = re.compile(r"^v?([0-9]+\.[0-9]+\.[0-9]+)$")
MAX_ARCHIVE = 100 * 1024 * 1024

def save_result(state, detail, version=None):
    result = {"state": state, "detail": str(detail)[:300], "version": version,
              "timestamp": int(time.time())}
    tmp = RESULT.with_suffix(".root-tmp")
    tmp.write_text(json.dumps(result) + "\n")
    os.chmod(tmp, 0o644)
    os.replace(tmp, RESULT)

def latest():
    req = urllib.request.Request(
        f"https://api.github.com/repos/{get_repository()}/releases/latest",
        headers={"User-Agent": "MMDVM-Multi-Updater", "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(req, timeout=20) as response:
        metadata = json.load(response)
    if metadata.get("draft") or metadata.get("prerelease"):
        raise RuntimeError("Release non stabile")
    version = metadata.get("tag_name", "")
    if not VERSION.fullmatch(version):
        raise RuntimeError("Tag release non valido")
    return version

def read_request():
    if REQUEST.is_symlink() or not REQUEST.is_file():
        return None
    if REQUEST.stat().st_size > 1024:
        raise RuntimeError("Richiesta troppo grande")
    payload = json.loads(REQUEST.read_text())
    if not isinstance(payload, dict) or set(payload) != {"version"}:
        raise RuntimeError("Richiesta non valida")
    version = payload["version"]
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise RuntimeError("Versione richiesta non valida")
    return version

def download(version):
    BASE.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = BASE / ("release-" + version + ".tar.gz")
    req = urllib.request.Request(archive_url(version), headers={"User-Agent": "MMDVM-Multi-Updater"})
    temp = path.with_suffix(".download")
    try:
        with urllib.request.urlopen(req, timeout=45) as response, temp.open("wb") as output:
            total = 0
            while True:
                block = response.read(65536)
                if not block:
                    break
                total += len(block)
                if total > MAX_ARCHIVE:
                    raise RuntimeError("Archivio troppo grande")
                output.write(block)
        os.replace(temp, path)
        return path
    finally:
        temp.unlink(missing_ok=True)

def run():
    # Do not consume requests while recovery is needed.
    if pending_transactions(BACKUPS):
        save_result("blocked", "Transazione incompleta: recupero necessario")
        return
    manual = read_request()
    mode = "manual"
    if SETTINGS.is_file() and not SETTINGS.is_symlink():
        try:
            mode = json.loads(SETTINGS.read_text()).get("mode", "manual")
        except (ValueError, OSError):
            mode = "manual"
    if not manual and mode != "automatic":
        return
    version = latest()
    if manual:
        if manual != version:
            raise RuntimeError("La versione richiesta non è più la release stabile più recente")
    else:
        installed = (app_root() / "VERSION").read_text().strip()
        if not VERSION.fullmatch(installed):
            return
        if tuple(map(int, VERSION.fullmatch(version).group(1).split("."))) <= tuple(map(int, VERSION.fullmatch(installed).group(1).split("."))):
            return
    # Mark manual request consumed before beginning; avoid repeated execution.
    if manual:
        REQUEST.unlink()
    save_result("running", "Preparazione release", version)
    archive = download(version)
    release = BASE / "releases" / VERSION.fullmatch(version).group(1)
    if not release.exists():
        extract_release(archive, version, release)
        # update_live requires an exhaustive SHA-256 manifest.
        entries = fingerprint(release)
        manifest = release / "MANIFEST.sha256"
        manifest.write_text("".join(
            f"{digest}  {name}\n" for name, digest in sorted(entries.items())
        ))
        os.chmod(manifest, 0o644)
    os.environ["MMDVM_ENABLE_REAL_UPDATE"] = "YES"
    install(release)
    save_result("completed", "Installazione e controlli completati", version)

def main():
    if os.geteuid() != 0:
        raise RuntimeError("Richiesto root")
    try:
        run()
    except Exception as exc:
        save_result("failed", exc)
        raise

if __name__ == "__main__":
    main()
