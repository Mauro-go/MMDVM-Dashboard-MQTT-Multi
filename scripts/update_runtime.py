"""Percorsi dell'installazione selezionata dall'installer; nessun fallback PROD/TEST."""
import json
import os
from pathlib import Path


def app_root():
    value = os.environ.get("MMDVM_APP_ROOT", "").strip()
    if value:
        root = Path(value).resolve(strict=True)
    else:
        # Gli script vengono installati in APP/scripts, non in /usr/local/lib.
        root = Path(__file__).resolve().parent.parent
    if not (root / "config" / "update-repository").is_file():
        raise RuntimeError("Origine aggiornamenti mancante in: " + str(root))
    if (root / "config" / "update-repository").is_symlink():
        raise RuntimeError("Origine aggiornamenti non valida: symlink")
    return root


def metadata():
    root = app_root()
    path = root / "config" / "update-runtime.json"
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("Configurazione updater mancante: " + str(path))
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise RuntimeError("Configurazione updater non valida")
    services = data.get("services")
    port = data.get("port")
    if not isinstance(services, list) or not services or any(not isinstance(x,str) or not x.endswith('.service') or '/' in x for x in services):
        raise RuntimeError("Servizi updater non validi")
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise RuntimeError("Porta updater non valida")
    return data


def base():
    root = app_root()
    # Lo stato di aggiornamento è separato per installazione, senza collisioni.
    return root / "data" / "updater-state"
