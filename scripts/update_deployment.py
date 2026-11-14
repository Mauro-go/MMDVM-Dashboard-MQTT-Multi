"""Profilo updater associato all'installazione, non al nome della directory."""
from pathlib import Path
from update_runtime import app_root, metadata

def detect_deployment(installation_path):
    if Path(installation_path).resolve() != app_root():
        raise ValueError("Directory diversa dall'installazione attiva")
    return metadata().get("mode", "multi")

def get_profile(mode):
    config = metadata()
    active = config.get("mode", "multi")
    if mode != active:
        raise ValueError("Profilo richiesto diverso da quello installato")
    return {"mode": active, "directory": str(app_root()), "port": config["port"], "services": tuple(config["services"])}
