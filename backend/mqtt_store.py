from node_paths import data_file
import json
import sqlite3
from pathlib import Path

DB = data_file("events.sqlite3")


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=10)
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('''CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        mode TEXT NOT NULL,
        action TEXT,
        payload TEXT NOT NULL
    )''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_events_time ON events(timestamp)')
    return con


def save(con, timestamp, mode, action, payload):
    con.execute('INSERT INTO events(timestamp,mode,action,payload) VALUES(?,?,?,?)',
                (timestamp, mode, action, json.dumps(payload, ensure_ascii=False)))
    con.commit()


def init_gateway_links(con):
    """Create the gateway state table in the current node database."""
    con.execute("""
        CREATE TABLE IF NOT EXISTS gateway_links (
            gateway TEXT PRIMARY KEY,
            action TEXT,
            target TEXT,
            reason TEXT,
            event_timestamp TEXT,
            received_at TEXT,
            payload TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS gateway_status (
            gateway TEXT PRIMARY KEY,
            message TEXT,
            event_timestamp TEXT,
            received_at TEXT,
            payload TEXT
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS gateway_link_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gateway TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT NOT NULL DEFAULT '',
            event_timestamp TEXT NOT NULL,
            received_at TEXT,
            payload TEXT
        )
    """)
    con.execute("""
        CREATE INDEX IF NOT EXISTS idx_gateway_link_history_time
        ON gateway_link_history(gateway, event_timestamp)
    """)
    con.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS idx_gateway_link_history_unique
        ON gateway_link_history(gateway, event_timestamp, action, target)
    """)
    con.commit()


def save_gateway_link(con, gateway, payload):
    """Store gateway link and status separately, without touching QSO events."""
    from datetime import datetime, timezone

    if not isinstance(payload, dict):
        return

    received_at = datetime.now(timezone.utc).isoformat()

    for kind, table in (
        ("link", "gateway_links"),
        ("status", "gateway_status"),
    ):
        event = payload.get(kind)
        if not isinstance(event, dict):
            continue

        timestamp = event.get("timestamp")
        if not timestamp:
            continue

        timestamp = str(timestamp)

        old = con.execute(
            f"SELECT event_timestamp FROM {table} WHERE gateway=?",
            (gateway,)
        ).fetchone()

        if old and old[0] and timestamp < str(old[0]):
            continue

        if kind == "link":
            target = (
                event.get("network")
                or event.get("reflector")
                or event.get("server")
                or ""
            )
            action = event.get("action") or event.get("state") or ""
            reason = event.get("reason") or ""

            con.execute("""
                INSERT INTO gateway_links
                (gateway, action, target, reason,
                 event_timestamp, received_at, payload)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(gateway) DO UPDATE SET
                    action=excluded.action,
                    target=excluded.target,
                    reason=excluded.reason,
                    event_timestamp=excluded.event_timestamp,
                    received_at=excluded.received_at,
                    payload=excluded.payload
            """, (
                gateway, str(action), str(target), str(reason),
                timestamp, received_at,
                json.dumps(payload, ensure_ascii=False)
            ))

        else:
            con.execute("""
                INSERT INTO gateway_status
                (gateway, message, event_timestamp, received_at, payload)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(gateway) DO UPDATE SET
                    message=excluded.message,
                    event_timestamp=excluded.event_timestamp,
                    received_at=excluded.received_at,
                    payload=excluded.payload
            """, (
                gateway, str(event.get("message") or ""),
                timestamp, received_at,
                json.dumps(payload, ensure_ascii=False)
            ))

    con.commit()


def save_gateway_connection(con, gateway, payload):
    """Store independent DMR network states from MQTT events."""
    from datetime import datetime, timezone

    if gateway != "dmrgateway" or not isinstance(payload, dict):
        return

    def parse_timestamp(value):
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    received_at = datetime.now(timezone.utc).isoformat()

    for kind in ("status", "link"):
        event = payload.get(kind)
        if not isinstance(event, dict):
            continue

        timestamp = str(event.get("timestamp") or "")
        event_time = parse_timestamp(timestamp)
        if event_time is None:
            continue

        target = ""
        action = ""
        reason = str(event.get("reason") or "")
        message = str(event.get("message") or "")

        if kind == "link":
            target = str(event.get("network") or "").strip()
            action = str(event.get("action") or "").lower()
            state = {
                "linking": "connected",
                "linked": "connected",
                "connected": "connected",
                "unlinked": "disconnected",
                "failed": "disconnected",
                "disconnected": "disconnected",
            }.get(action)
        else:
            state = None
            for prefix, value in (
                ("Logged into DMR Network:", "connected"),
                ("Opening DMR Network:", "connecting"),
                ("Failed connection into DMR Network:", "disconnected"),
            ):
                if message.startswith(prefix):
                    target = message[len(prefix):].strip()
                    state = value
                    action = value
                    break

        if not target or not state:
            continue

        old = con.execute("""
            SELECT event_timestamp, state, action
            FROM gateway_connections
            WHERE gateway=? AND target=?
        """, (gateway, target)).fetchone()

        if old:
            old_time = parse_timestamp(old[0])
            if old_time:
                if event_time < old_time:
                    continue

                if event_time == old_time:
                    # Link events take precedence over status messages
                    # describing an attempted connection.
                    priority = {
                        "connecting": 0,
                        "connected": 1,
                        "disconnected": 2,
                    }
                    old_priority = priority.get(old[1], -1)
                    new_priority = priority.get(state, -1)
                    if new_priority < old_priority:
                        continue

        con.execute("""
            INSERT INTO gateway_connections
            (gateway, target, state, action, reason,
             event_timestamp, received_at,
             status_message, status_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gateway, target) DO UPDATE SET
                state=excluded.state,
                action=excluded.action,
                reason=excluded.reason,
                event_timestamp=excluded.event_timestamp,
                received_at=excluded.received_at,
                status_message=excluded.status_message,
                status_timestamp=excluded.status_timestamp
        """, (
            gateway, target, state, action, reason,
            timestamp, received_at,
            message if kind == "status" else "",
            timestamp if kind == "status" else None
        ))

    con.commit()


def save_dstar_link_history(con, gateway, payload):
    """Registra la cronologia dei collegamenti DStarGateway."""

    from datetime import datetime, timezone

    if gateway != "dstargateway" or not isinstance(payload, dict):
        return

    event = payload.get("link")
    if not isinstance(event, dict):
        return

    timestamp = event.get("timestamp")
    if not timestamp:
        return

    action = str(event.get("action") or event.get("state") or "").strip().lower()
    if not action:
        return

    target = str(
        event.get("network")
        or event.get("reflector")
        or event.get("server")
        or ""
    ).strip()

    received_at = datetime.now(timezone.utc).isoformat()

    con.execute("""
        INSERT OR IGNORE INTO gateway_link_history
        (gateway, action, target, event_timestamp, received_at, payload)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        gateway,
        action,
        target,
        str(timestamp),
        received_at,
        json.dumps(payload, ensure_ascii=False),
    ))

    con.commit()
