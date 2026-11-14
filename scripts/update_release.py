#!/usr/bin/env python3
"""Validazione ed estrazione selettiva delle release GitHub."""

import os
import re
import shutil
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

from update_origin import get_repository
VERSION_RE = re.compile(r"^v?[0-9]+\.[0-9]+\.[0-9]+$")

COMPONENTS = {"backend", "frontend", "admin", "VERSION"}

REQUIRED_FILES = {
    "VERSION",
    "backend/app.py",
    "backend/admin_api.py",
    "backend/update_checker.py",
    "frontend/index.html",
    "admin/admin.html",
}

PROTECTED = {
    "dashboard.ini",
    "mqtt-settings.json",
    "telegram-settings.json",
    "events.sqlite3",
    "admin.json",
    "session-secret",
    "setup-token",
}

MAX_MEMBERS = 5000
MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_TOTAL_SIZE = 100 * 1024 * 1024


def validate_version(version):
    if not isinstance(version, str):
        raise ValueError("Versione non valida")
    if not VERSION_RE.fullmatch(version):
        raise ValueError("Formato versione non valido")
    return version


def archive_url(version):
    version = validate_version(version)
    return (
        f"https://github.com/{get_repository()}/"
        f"archive/refs/tags/{version}.tar.gz"
    )


def inspect_archive(filename, expected_version):
    """Controlla l'intero archivio e individua i file aggiornabili."""
    expected = validate_version(expected_version).removeprefix("v")
    seen = set()
    files = set()
    directories = set()
    selected = {}
    roots = set()
    total_size = 0

    with tarfile.open(filename, "r:gz") as archive:
        members = archive.getmembers()

        if len(members) > MAX_MEMBERS:
            raise ValueError("Troppi elementi nell'archivio")

        for member in members:
            name = member.name

            if (
                not name
                or name.startswith("/")
                or "\\" in name
                or "//" in name
                or name.endswith("/.")
            ):
                raise ValueError("Percorso non sicuro")

            parts = name.split("/")

            if any(part in {"", ".", ".."} for part in parts):
                raise ValueError("Percorso non sicuro")

            if not all(parts):
                raise ValueError("Percorso vuoto")

            path = PurePosixPath(name)
            canonical = path.as_posix()

            if canonical != name.rstrip("/"):
                raise ValueError("Percorso non canonico")

            roots.add(parts[0])

            if len(roots) > 1:
                raise ValueError("Directory principale multipla")

            if canonical in seen:
                raise ValueError("Elemento duplicato")

            seen.add(canonical)

            if member.issym() or member.islnk():
                raise ValueError("Link non consentito")

            if not (member.isfile() or member.isdir()):
                raise ValueError("Tipo di file non consentito")

            if member.isfile():
                files.add(canonical)

                if member.size > MAX_FILE_SIZE:
                    raise ValueError("File troppo grande")

                total_size += member.size

                if total_size > MAX_TOTAL_SIZE:
                    raise ValueError("Archivio troppo grande")
            else:
                directories.add(canonical)

            for index in range(1, len(parts)):
                parent = "/".join(parts[:index])

                if parent in files:
                    raise ValueError("File utilizzato come directory")

            if len(parts) < 2:
                continue

            relative_parts = parts[1:]
            component = relative_parts[0]
            relative = "/".join(relative_parts)

            if component not in COMPONENTS:
                continue

            if component == "VERSION":
                if len(relative_parts) != 1 or not member.isfile():
                    raise ValueError("VERSION non valida")

            if component != "VERSION" and len(relative_parts) == 1:
                if not member.isdir():
                    raise ValueError("Componente non directory")

            if any(
                part in {"config", "data"} | PROTECTED
                for part in relative_parts[1:]
            ):
                raise ValueError("Percorso applicativo protetto")

            if member.isfile():
                selected[relative] = member

        if files & directories:
            raise ValueError("Collisione file/directory")

        for file_path in files:
            if any(
                other.startswith(file_path + "/")
                for other in seen
            ):
                raise ValueError("File utilizzato come directory")

        missing = REQUIRED_FILES - set(selected)

        if missing:
            raise ValueError(
                "File mancanti: " + ", ".join(sorted(missing))
            )

        version_member = selected["VERSION"]

        if version_member.size > 100:
            raise ValueError("VERSION troppo grande")

        stream = archive.extractfile(version_member)

        if stream is None:
            raise ValueError("VERSION non leggibile")

        version = stream.read(101).decode("utf-8").strip()

        if version != expected:
            raise ValueError("VERSION non corrispondente")

    return sorted(selected)


def validate_archive(filename, expected_version):
    inspect_archive(filename, expected_version)
    return True


def extract_release(filename, expected_version, destination):
    """Estrae soltanto i componenti applicativi verificati."""
    destination = Path(destination)

    if destination.exists() or destination.is_symlink():
        raise ValueError("Destinazione già esistente")

    selected = inspect_archive(filename, expected_version)

    destination.parent.mkdir(parents=True, exist_ok=True)

    staging = Path(tempfile.mkdtemp(
        prefix=".release-",
        dir=destination.parent,
    ))

    try:
        for component in ("backend", "frontend", "admin"):
            (staging / component).mkdir(mode=0o755)

        with tarfile.open(filename, "r:gz") as archive:
            members = {
                "/".join(member.name.split("/")[1:]): member
                for member in archive.getmembers()
                if member.isfile()
                and len(member.name.split("/")) > 1
            }

            for relative in selected:
                member = members[relative]
                target = staging.joinpath(*relative.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)

                stream = archive.extractfile(member)

                if stream is None:
                    raise ValueError("File non leggibile")

                with target.open("xb") as output:
                    shutil.copyfileobj(stream, output)

                os.chmod(target, 0o644)

        if not all(
            (staging / name).exists()
            for name in COMPONENTS
        ):
            raise ValueError("Release incompleta")

        os.replace(staging, destination)

    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise

    return destination


def main():
    if len(sys.argv) != 2:
        print("Uso: update_release.py VERSIONE", file=sys.stderr)
        return 2

    try:
        print(archive_url(sys.argv[1]))
    except ValueError as exc:
        print(f"ERRORE: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
