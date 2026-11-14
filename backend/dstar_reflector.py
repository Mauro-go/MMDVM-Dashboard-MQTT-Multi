"""Attribuzione del reflector D-Star dal collegamento del gateway."""

import sqlite3
from contextlib import closing

from node_paths import data_file


def resolve_dstar_reflector(source, reflector="", db=None):
    """Restituisce il reflector esplicito o quello collegato per traffico NET."""

    explicit = str(reflector or "").strip()
    if explicit:
        return explicit

    if str(source or "").strip().lower() != "network":
        return ""

    database = db if db is not None else data_file("events.sqlite3")

    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as con:
            row = con.execute(
                """
                SELECT action, target
                FROM gateway_links
                WHERE gateway = 'dstargateway'
                """
            ).fetchone()
    except (sqlite3.Error, OSError):
        return ""

    if not row:
        return ""

    action, target = row

    if str(action or "").strip().lower() not in ("linking", "linked", "connected"):
        return ""

    return str(target or "").strip()


def resolve_dstar_reflector_at(source, timestamp, reflector="", db=None):
    """Risolve il reflector usando la cronologia disponibile al timestamp."""

    explicit = str(reflector or "").strip()
    if explicit:
        return explicit

    if str(source or "").strip().lower() != "network":
        return ""

    if not timestamp:
        return ""

    database = db if db is not None else data_file("events.sqlite3")

    try:
        with closing(sqlite3.connect(f"file:{database}?mode=ro", uri=True)) as con:
            row = con.execute(
                """
                SELECT action, target
                FROM gateway_link_history
                WHERE gateway = 'dstargateway'
                  AND julianday(event_timestamp) <= julianday(?)
                ORDER BY julianday(event_timestamp) DESC, id DESC
                LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
    except (sqlite3.Error, OSError):
        return ""

    if not row:
        return ""

    action, target = row

    if str(action or "").strip().lower() not in (
        "linking", "linked", "connected"
    ):
        return ""

    return str(target or "").strip()
