"""Repository di origine dell'installazione: non usare mai fallback impliciti."""
from pathlib import Path

from update_runtime import app_root
APP_ROOT = app_root()
REPOSITORY_FILE = APP_ROOT / "config" / "update-repository"
ALLOWED_REPOSITORIES = frozenset({
    "Mauro-go/MMDVM-Dashboard-MQTT-Multi",
})


def get_repository(path=None):
    origin = Path(path) if path is not None else REPOSITORY_FILE
    if origin.is_symlink() or not origin.is_file():
        raise RuntimeError("Repository aggiornamenti non configurato: " + str(origin))
    repository = origin.read_text(encoding="utf-8").strip()
    if repository not in ALLOWED_REPOSITORIES:
        raise RuntimeError("Repository aggiornamenti non autorizzato: " + repository[:120])
    return repository
