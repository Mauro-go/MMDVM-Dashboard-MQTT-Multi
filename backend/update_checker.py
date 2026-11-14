#!/usr/bin/env python3
"""Controllo delle release ufficiali MMDVM Dashboard MQTT."""

import json
import re
import io
import zipfile
import hashlib
import stat
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

APP_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = APP_ROOT / "VERSION"
from update_origin import get_repository

VERSION_PATTERN = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)$"
)


def parse_version(value):
    match = VERSION_PATTERN.fullmatch(str(value).strip())
    return tuple(map(int, match.groups())) if match else None


def installed_version():
    try:
        value = VERSION_FILE.read_text(encoding="utf-8").strip()
        return value or None
    except OSError:
        return None



AGENT_ASSET_NAME = "MMDVM-MQTT-Agent.zip"
MAX_AGENT_ARCHIVE_SIZE = 5 * 1024 * 1024



def inspect_agent_archive(data):
    """Verifica struttura, contenuto e integrità del pacchetto Agent."""
    if not isinstance(data, bytes) or not data:
        raise ValueError("Archivio Agent vuoto")

    if len(data) > MAX_AGENT_ARCHIVE_SIZE:
        raise ValueError("Archivio Agent troppo grande")

    required = {
        "VERSION",
        "agent.py",
        "install.sh",
        "mmdvm-agent.service",
        "detect-mmdvm-config.py",
        "agent.json.example",
        "broker.json.example",
        "update-agent.sh",
    }

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        names = [item.filename for item in entries]

        if len(names) != len(set(names)):
            raise ValueError("Elementi duplicati nell'archivio Agent")

        if set(names) != required:
            raise ValueError("Struttura del pacchetto Agent non valida")

        total_size = 0

        for item in entries:
            name = item.filename

            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or ".." in name.split("/")
                or item.is_dir()
                or item.flag_bits & 1
            ):
                raise ValueError("Elemento ZIP non consentito")

            mode = item.external_attr >> 16

            if stat.S_ISLNK(mode):
                raise ValueError("Link simbolico non consentito")

            if item.file_size > 2 * 1024 * 1024:
                raise ValueError("File Agent troppo grande")

            total_size += item.file_size

            if total_size > 5 * 1024 * 1024:
                raise ValueError("Contenuto Agent troppo grande")

        if archive.testzip() is not None:
            raise ValueError("CRC archivio Agent non valido")

        version = archive.read("VERSION").decode("utf-8").strip()

        if not parse_version(version):
            raise ValueError("Versione Agent non valida")

        source = archive.read("agent.py").decode("utf-8")


        source = archive.read("agent.py").decode("utf-8")

        declared_version = None

        for line in source.splitlines():
            if line.strip().startswith("VERSION"):
                key, separator, value = line.partition("=")

                if separator and key.strip() == "VERSION":
                    declared_version = value.strip().strip(chr(39) + chr(34))
                    break

        if declared_version != version:
            raise ValueError("Versione Agent non corrispondente")


    return {
        "version": version,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
    }


def inspect_agent_release_asset(release):
    """Scarica e verifica l'asset Agent della release GitHub."""
    assets = release.get("assets", [])

    asset = next(
        (
            item for item in assets
            if isinstance(item, dict)
            and item.get("name") == AGENT_ASSET_NAME
        ),
        None,
    )

    if asset is None:
        return {
            "status": "missing",
            "version": None,
            "url": None,
            "sha256": None,
            "size": None,
            "error": "Agent package not found in release",
        }

    url = asset.get("browser_download_url", "")

    if not isinstance(url, str) or not url.startswith(
        f"https://github.com/{get_repository()}/releases/download/"
    ):
        raise ValueError("URL asset Agent non autorizzato")

    declared_size = asset.get("size")

    if not isinstance(declared_size, int):
        raise ValueError("Dimensione asset non valida")

    if declared_size <= 0 or declared_size > MAX_AGENT_ARCHIVE_SIZE:
        raise ValueError("Dimensione asset fuori limite")

    request = Request(
        url,
        headers={
            "Accept": "application/octet-stream",
            "User-Agent": "MMDVM-Dashboard-MQTT-Multi",
        },
    )

    with urlopen(request, timeout=20) as response:
        data = response.read(MAX_AGENT_ARCHIVE_SIZE + 1)

    if len(data) != declared_size:
        raise ValueError("Dimensione asset non corrispondente")

    result = inspect_agent_archive(data)

    return {
        "status": "available",
        "version": result["version"],
        "url": url,
        "sha256": result["sha256"],
        "size": result["size"],
        "error": None,
    }


def check_updates():
    installed = installed_version()

    result = {
        "installed": installed,
        "latest": None,
        "status": "unknown",
        "release_url": None,
        "release_notes": None,
        "error": None,
    }

    try:
        repository = get_repository()
    except (OSError, RuntimeError) as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    request = Request(
        f"https://api.github.com/repos/{repository}/releases/latest",
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "MMDVM-Dashboard-MQTT-Multi",
        },
    )

    try:
        with urlopen(request, timeout=10) as response:
            release = json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            result["status"] = "no_releases"
        else:
            result["status"] = "error"
            result["error"] = f"GitHub HTTP {exc.code}"
        return result
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        result["status"] = "error"
        result["error"] = str(exc)
        return result

    latest = str(release.get("tag_name") or "").strip()
    result["latest"] = latest or None

    url = release.get("html_url")
    if isinstance(url, str) and url.startswith(
        f"https://github.com/{repository}/releases/"
    ):
        result["release_url"] = url

    result["release_notes"] = str(release.get("body") or "")[:12000]

    result["agent_package"] = {
        "status": "unavailable",
        "version": None,
        "url": None,
        "sha256": None,
        "size": None,
        "error": None,
    }

    try:
        result["agent_package"] = inspect_agent_release_asset(release)
        result["agent_package_version"] = result["agent_package"]["version"]
    except Exception as exc:
        result["agent_package"]["error"] = str(exc)
        result["agent_package"]["status"] = "error"
        result["agent_package_version"] = None

    current_number = parse_version(installed) if installed else None
    latest_number = parse_version(latest)

    if not latest_number:
        result["status"] = "unknown"
    elif not current_number:
        result["status"] = "installed_unknown"
    elif latest_number > current_number:
        result["status"] = "update_available"
    else:
        result["status"] = "up_to_date"

    return result


if __name__ == "__main__":
    print(json.dumps(check_updates(), indent=2, ensure_ascii=False))


def compare_agent_versions(installed_versions, package_version):
    """Confronta le versioni degli Agent remoti con quella del pacchetto."""
    if not isinstance(package_version, str) or not parse_version(package_version):
        return []

    results = []

    for node_id, installed in installed_versions.items():
        if not isinstance(node_id, str):
            continue

        if not isinstance(installed, str) or not parse_version(installed):
            continue

        if parse_version(installed) != parse_version(package_version):
            results.append({
                "node_id": node_id,
                "installed": installed,
                "available": package_version,
                "status": "agent_update_required"
            })

    return results
