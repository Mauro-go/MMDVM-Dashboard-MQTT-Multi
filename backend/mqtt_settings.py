from node_paths import data_file
"""Private MQTT connection settings for dashboard collector and admin."""
import json
import os
import tempfile
from pathlib import Path

SETTINGS = data_file("mqtt-settings.json")
DEFAULTS = {"host": "127.0.0.1", "port": 1883, "topic": "mmdvm/+/json", "username": "", "password": "", "tls": False}


def load():
    if not SETTINGS.exists():
        return dict(DEFAULTS)
    with SETTINGS.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("Invalid MQTT settings")
    return {**DEFAULTS, **data}


def public():
    data = load()
    return {k: data.get(k) for k in ("host", "port", "topic", "username", "tls")} | {"password_set": bool(data.get("password")), "agent_password_set": bool(data.get("agent_password")), "agent_same_broker": data.get("agent_same_broker", True), "agent_host": data.get("agent_host", data.get("host", "127.0.0.1")), "agent_port": data.get("agent_port", data.get("port", 1883)), "agent_username": data.get("agent_username", ""), "agent_tls": data.get("agent_tls", False)}


def save(changes):
    if not isinstance(changes, dict) or set(changes) - {"host", "port", "topic", "username", "password", "clear_password", "tls", "agent_same_broker", "agent_host", "agent_port", "agent_username", "agent_password", "agent_tls", "clear_agent_password"}:
        raise ValueError("Invalid MQTT fields")
    data = load()
    for key in ("host", "topic", "username"):
        if key in changes:
            val = changes[key]
            if not isinstance(val, str) or len(val) > 255 or (key != "username" and not val.strip()):
                raise ValueError("Invalid MQTT " + key)
            if any(ord(c) < (32 if key == "username" else 33) for c in val):
                raise ValueError("Invalid MQTT " + key)
            data[key] = val
    if "port" in changes:
        port = changes["port"]
        if isinstance(port, bool) or not str(port).isdigit() or not 1 <= int(port) <= 65535:
            raise ValueError("Invalid MQTT port")
        data["port"] = int(port)
    if "tls" in changes:
        if not isinstance(changes["tls"], bool):
            raise ValueError("Invalid MQTT TLS")
        data["tls"] = changes["tls"]
    if "clear_password" in changes and not isinstance(changes["clear_password"], bool):
        raise ValueError("Invalid clear_password")
    if changes.get("clear_password"):
        data["password"] = ""
    elif "password" in changes:
        val = changes["password"]
        if not isinstance(val, str) or len(val) > 1024 or any(ord(c) < 32 for c in val):
            raise ValueError("Invalid MQTT password")
        if val:
            data["password"] = val
    if data.get("password") and not data.get("username"):
        raise ValueError("MQTT username required when password is set")
    for key in ("agent_same_broker", "agent_tls", "clear_agent_password"):
        if key in changes and not isinstance(changes[key], bool):
            raise ValueError("Invalid " + key)
    for key in ("agent_host", "agent_username"):
        if key in changes:
            val = changes[key]
            if not isinstance(val, str) or len(val) > 255 or (key == "agent_host" and not val.strip()):
                raise ValueError("Invalid " + key)
            if any(ord(c) < (32 if key == "agent_username" else 33) for c in val):
                raise ValueError("Invalid " + key)
            data[key] = val
    if "agent_port" in changes:
        val = changes["agent_port"]
        if isinstance(val, bool) or not str(val).isdigit() or not 1 <= int(val) <= 65535:
            raise ValueError("Invalid Agent MQTT port")
        data["agent_port"] = int(val)
    for key in ("agent_same_broker", "agent_tls"):
        if key in changes:
            data[key] = changes[key]
    if changes.get("clear_agent_password"):
        data["agent_password"] = ""
    elif "agent_password" in changes:
        val = changes["agent_password"]
        if not isinstance(val, str) or len(val) > 1024 or any(ord(c) < 32 for c in val):
            raise ValueError("Invalid Agent MQTT password")
        if val:
            data["agent_password"] = val
    if not data.get("agent_same_broker", True):
        if not data.get("agent_host", data["host"]):
            raise ValueError("Agent MQTT host required")
        if data.get("agent_password") and not data.get("agent_username"):
            raise ValueError("Agent MQTT username required when password is set")
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".mqtt-", dir=str(SETTINGS.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), 0o600)
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, SETTINGS)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return public()
