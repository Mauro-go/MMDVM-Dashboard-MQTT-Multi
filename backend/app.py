#!/usr/bin/env python3

import os
import sys
import time
import subprocess
import configparser
import psutil
import config as dashboard_config
import remote_agent

from flask import Flask, jsonify, send_from_directory

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_DIR = os.path.join(BASE_DIR, "frontend")

sys.path.insert(0, os.path.join(BASE_DIR, "backend"))
from parser import load_live, get_repeater_callsign, get_operator_name, get_dmr_ids
from mqtt_sessions import reconstruct, dt
from dstar_reflector import resolve_dstar_reflector_at
from datetime import datetime, timezone

app = Flask(__name__)

from admin_api import admin_bp, configure_admin_app
configure_admin_app(app)
app.register_blueprint(admin_bp)


def read_repeater_info():
    if remote_agent.enabled():
        return remote_agent.repeater_info()
    info = {
        "callsign": get_repeater_callsign() or "MMDVM",
        "location": "",
        "description": "",
        "rx_frequency": "",
        "tx_frequency": "",
        "power": "",
    }

    try:
        cfg = configparser.ConfigParser(strict=False)
        cfg.optionxform = str
        cfg.read(dashboard_config.get_mmdvm_ini())

        if cfg.has_section("Info"):
            info["location"] = cfg.get("Info", "Location", fallback="")
            info["description"] = cfg.get("Info", "Description", fallback="")
            info["rx_frequency"] = cfg.get("Info", "RXFrequency", fallback="")
            info["tx_frequency"] = cfg.get("Info", "TXFrequency", fallback="")
            info["power"] = cfg.get("Info", "Power", fallback="")
    except Exception:
        pass

    return info


def frequency_mhz(value):
    try:
        return "{:.4f}".format(int(value) / 1000000.0)
    except Exception:
        return ""


def system_info():
    if remote_agent.enabled():
        return remote_agent.system_info()
    try:
        temp = psutil.sensors_temperatures()
        cpu_temp = None

        for values in temp.values():
            if values:
                cpu_temp = values[0].current
                break
    except Exception:
        cpu_temp = None

    disk = psutil.disk_usage("/")

    return {
        "cpu": round(psutil.cpu_percent(interval=None), 1),
        "memory": round(psutil.virtual_memory().percent, 1),
        "disk": round(disk.percent, 1),
        "temperature": cpu_temp,
        "uptime": int(time.time() - psutil.boot_time()),
    }


# Servizi radio/gateway supportati.
# Vengono mostrati solo quelli realmente installati sulla macchina.
SERVICE_CANDIDATES = [
    ("mmdvmhost.service",   "MMDVMHost"),
    ("dmrgateway.service",  "DMRGateway"),
    ("ysfgateway.service",  "YSFGateway"),
    ("dstargateway.service","DStarGateway"),
    ("ircddbgateway.service","ircDDBGateway"),
    ("p25gateway.service",  "P25Gateway"),
    ("nxdngateway.service", "NXDNGateway"),
    ("m17gateway.service",  "M17Gateway"),
    ("aprsgateway.service", "APRSGateway"),
]

def service_exists(unit):
    r = subprocess.run(
        ["systemctl", "show", unit, "--property=LoadState", "--value"],
        capture_output=True,
        text=True
    )

    return r.stdout.strip() not in ("", "not-found")


def service_state(unit):
    r = subprocess.run(
        ["systemctl", "is-active", unit],
        capture_output=True,
        text=True
    )

    state = r.stdout.strip()

    return state if state else "unknown"


def service_enabled(unit):
    r = subprocess.run(
        ["systemctl", "is-enabled", unit],
        capture_output=True,
        text=True
    )

    return r.stdout.strip()


@app.route("/api/services")
def api_services():
    if remote_agent.enabled():
        return jsonify(remote_agent.services(SERVICE_CANDIDATES))
    services = []

    for unit, name in SERVICE_CANDIDATES:
        if not service_exists(unit):
            continue

        state = service_state(unit)
        enabled = service_enabled(unit)

        services.append({
            "name": name,
            "unit": unit,
            "state": state,
            "enabled": enabled,
            "active": state == "active"
        })

    return jsonify(services)



@app.route("/api/gateway-links")
def api_gateway_links():
    """Return gateway events and connection evidence for the current node."""
    import sqlite3
    from datetime import datetime, timezone
    from node_paths import data_file

    gateways = (
        "dmrgateway", "ysfgateway",
        "dstargateway", "aprsgateway",
    )

    def parse_time(value):
        if not value:
            return None
        try:
            dt = datetime.fromisoformat(
                str(value).replace("Z", "+00:00")
            )
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None

    results = {
        name: {
            "gateway": name,
            "action": "",
            "target": "",
            "reason": "",
            "event_timestamp": None,
            "received_at": None,
            "fresh": False,
            "state": "unknown",
            "status_message": "",
            "status_timestamp": None,
            "verified": False,
        }
        for name in gateways
    }

    # Remote mode: Agent handles non-DMR gateways.
    # DMR connections are read separately from MQTT SQLite state.
    if remote_agent.enabled():
        snapshot = remote_agent.snapshot()
        online = snapshot.get("online") is True
        telemetry = snapshot.get("telemetry", {}) if online else {}
        source = telemetry.get("gateways", {})
        remote_results = []

        for name in gateways:
            if name == "dmrgateway":
                continue

            gateway_data = source.get(name, {})
            if not isinstance(gateway_data, dict):
                gateway_data = {}

            service = gateway_data.get("service", "unknown")
            connections = gateway_data.get("connections", {})
            if not isinstance(connections, dict):
                connections = {}

            if not connections:
                remote_results.append({
                    **results[name],
                    "service_state": service,
                })
                continue

            for target, info in connections.items():
                if not isinstance(info, dict):
                    continue

                raw_state = info.get("state", "unknown")
                available = online and service == "active"

                # Preserve the last observed state while the Agent
                # and gateway service are available.
                if not available:
                    state = "unknown"
                elif raw_state == "connected":
                    state = "connected"
                elif raw_state == "disconnected":
                    state = "disconnected"
                elif raw_state in ("linking", "connecting"):
                    state = "connecting"
                else:
                    state = "unknown"

                is_fresh = available
                verified = available and state == "connected"

                event_time = info.get("timestamp")
                remote_results.append({
                    "gateway": name,
                    "action": raw_state,
                    "target": str(target),
                    "reason": "",
                    "event_timestamp": event_time,
                    "received_at": None,
                    "fresh": is_fresh,
                    "state": state,
                    "status_message": info.get("message", "") if verified else "",
                    "status_timestamp": event_time if verified else None,
                    "verified": verified,
                    "service_state": service,
                })

        service = source.get("dmrgateway", {}).get("service", "unknown")
        db_path = data_file("events.sqlite3")
        dmr_rows = []

        if db_path.exists():
            try:
                with sqlite3.connect(str(db_path), timeout=5) as con:
                    dmr_rows = con.execute("""
                        SELECT target, state, action, reason,
                               event_timestamp, received_at,
                               status_message, status_timestamp
                        FROM gateway_connections
                        WHERE gateway = 'dmrgateway'
                        ORDER BY target
                    """).fetchall()
            except sqlite3.Error:
                app.logger.exception("Unable to read DMR connections")

        for (target, stored_state, action, reason,
             timestamp, received, message, status_timestamp) in dmr_rows:

            available = online and service == "active"
            state = stored_state if available else "unknown"

            # Last observed MQTT state, not a live connection probe.
            # "fresh" indicates the gateway service is currently available,
            # not that the network event is recent.
            last_observed = state == "connected"

            remote_results.append({
                "gateway": "dmrgateway",
                "action": action,
                "target": target,
                "reason": reason,
                "event_timestamp": timestamp,
                "received_at": received,
                "fresh": available,
                "state": state,
                "status_message": message,
                "status_timestamp": status_timestamp,
                "verified": False,
                "last_observed_connected": last_observed,
                "service_state": service,
            })

        if not dmr_rows:
            remote_results.append({
                **results["dmrgateway"],
                "service_state": service,
            })

        return jsonify(remote_results)

    db_path = data_file("events.sqlite3")
    if not db_path.exists():
        return jsonify(list(results.values()))

    try:
        with sqlite3.connect(str(db_path), timeout=5) as con:
            links = con.execute("""
                SELECT gateway, action, target, reason,
                       event_timestamp, received_at
                FROM gateway_links
            """).fetchall()

            statuses = con.execute("""
                SELECT gateway, message, event_timestamp
                FROM gateway_status
            """).fetchall()

    except sqlite3.Error:
        app.logger.exception("Unable to read gateway events")
        return jsonify(list(results.values()))

    now = datetime.now(timezone.utc)

    for gateway, action, target, reason, timestamp, received in links:
        if gateway not in results:
            continue

        action = str(action or "").lower()
        event_time = parse_time(timestamp)

        fresh = False
        if event_time:
            age = (now - event_time).total_seconds()
            fresh = 0 <= age <= 120

        state = "unknown"

        if action in ("failed", "unlinked", "disconnected"):
            state = "disconnected" if fresh else "unknown"
        elif action in ("linking", "relinking", "connecting"):
            state = "connecting" if fresh else "unknown"
        elif action in ("linked", "connected"):
            state = "connected" if fresh else "unknown"

        results[gateway].update({
            "action": action,
            "target": target or "",
            "reason": reason or "",
            "event_timestamp": timestamp,
            "received_at": received,
            "fresh": fresh,
            "state": state,
        })

    for gateway, message, timestamp in statuses:
        if gateway not in results:
            continue

        message = str(message or "")
        results[gateway]["status_message"] = message
        results[gateway]["status_timestamp"] = timestamp

        if gateway != "dmrgateway":
            continue

        prefix = "Logged into DMR Network:"
        if not message.startswith(prefix):
            continue

        status_time = parse_time(timestamp)
        link_time = parse_time(
            results[gateway]["event_timestamp"]
        )

        if not status_time:
            continue

        # A later failure or unlink must override an older login.
        if link_time and status_time < link_time:
            continue

        network = message[len(prefix):].strip()
        if network:
            results[gateway]["target"] = network

        # This confirms a successful login at the event timestamp.
        # It is not a live heartbeat proving the link is still active.
        results[gateway]["state"] = "connected"
        results[gateway]["verified"] = True

    return jsonify(list(results.values()))

def read_mmdvm_modes():
    if remote_agent.enabled():
        return remote_agent.modes()
    """Legge le modalità abilitate direttamente da MMDVM.ini."""
    modes = {
        "dmr": False,
        "dstar": False,
        "ysf": False,
        "fm": False,
        "p25": False,
        "nxdn": False,
        "m17": False,
        "pocsag": False,
        "ax25": False,
    }

    sections = {
        "dmr": "DMR",
        "dstar": "D-Star",
        "ysf": "System Fusion",
        "fm": "FM",
        "p25": "P25",
        "nxdn": "NXDN",
        "m17": "M17",
        "pocsag": "POCSAG",
        "ax25": "AX.25",
    }

    cfg = configparser.ConfigParser(strict=False)
    cfg.read(dashboard_config.get_mmdvm_ini())

    for mode, section in sections.items():
        modes[mode] = cfg.getboolean(
            section, "Enable", fallback=False
        )

    return modes



def local_radio_settings():
    """Legge MMDVM.ini e normalizza i dati radio come l'Agent remoto."""
    from radio_info import normalize_radio

    cfg = configparser.ConfigParser(
        interpolation=None,
        strict=False
    )
    cfg.optionxform = str

    path = dashboard_config.get_mmdvm_ini()

    if not cfg.read(path):
        return {}

    sections = ("Info", "FM", "DMR")
    fields = {
        section: dict(cfg.items(section))
        for section in sections
        if cfg.has_section(section)
    }

    modes = {
        section: cfg.getboolean(
            section, "Enable", fallback=False
        )
        for section in ("FM", "DMR")
    }

    return normalize_radio(fields, modes)



@app.route("/api/config")
def api_config():
    result = {
        "dashboard": {
            "language": dashboard_config.get("dashboard", "language"),
            "title": dashboard_config.get("dashboard", "title"),
        },
        "repeater": {
            "callsign": dashboard_config.get("repeater", "callsign"),
            "location": dashboard_config.get("repeater", "location"),
            "city": dashboard_config.get("repeater", "city"),
            "locator": dashboard_config.get("repeater", "locator"),
            "altitude": dashboard_config.get("repeater", "altitude"),
            "tx_frequency": dashboard_config.get("repeater", "tx_frequency"),
            "rx_frequency": dashboard_config.get("repeater", "rx_frequency"),
            "shift": dashboard_config.get("repeater", "shift"),
            "power": dashboard_config.get("repeater", "power"),
            "ctcss": dashboard_config.get("repeater", "ctcss"),
            "dmr_color_code": dashboard_config.get("repeater", "dmr_color_code"),
        },
        "modes": read_mmdvm_modes(),
        "station": {
            "organization": dashboard_config.get("station", "organization"),
            "organization_description": dashboard_config.get("station", "organization_description"),
            "maintainers": dashboard_config.get("station", "maintainers"),
            "collaboration": dashboard_config.get("station", "collaboration"),
        },
        "hardware": {
            "radio": dashboard_config.get("hardware", "radio"),
            "modem": dashboard_config.get("hardware", "modem"),
            "computer": dashboard_config.get("hardware", "computer"),
            "antenna": dashboard_config.get("hardware", "antenna"),
        },
        "images": {
            "banner": dashboard_config.get("images", "banner"),
            "repeater": dashboard_config.get("images", "repeater"),
            "logo": dashboard_config.get("images", "logo"),
        },
        "links": {
            "dstar_dashboard": dashboard_config.get("links", "dstar_dashboard"),
            "dstar_dashboard_label": dashboard_config.get("links", "dstar_dashboard_label"),
            "mmdvmhost_url": dashboard_config.get("links", "mmdvmhost_url"),
            "mmdvmhost_label": dashboard_config.get("links", "mmdvmhost_label"),
            "dmrgateway_url": dashboard_config.get("links", "dmrgateway_url"),
            "dmrgateway_label": dashboard_config.get("links", "dmrgateway_label"),
            "ysfgateway_url": dashboard_config.get("links", "ysfgateway_url"),
            "ysfgateway_label": dashboard_config.get("links", "ysfgateway_label"),
            "ircddbgateway_url": dashboard_config.get("links", "ircddbgateway_url"),
            "ircddbgateway_label": dashboard_config.get("links", "ircddbgateway_label"),
            "m17gateway_url": dashboard_config.get("links", "m17gateway_url"),
            "m17gateway_label": dashboard_config.get("links", "m17gateway_label"),
            "aprsgateway_url": dashboard_config.get("links", "aprsgateway_url"),
            "aprsgateway_label": dashboard_config.get("links", "aprsgateway_label"),
        },
    }
    if remote_agent.enabled():
        automatic = remote_agent.repeater_settings()
        result["agent"] = remote_agent.metadata()
    else:
        automatic = local_radio_settings()

    # Solo i valori automatici effettivamente presenti prevalgono.
    # Gli altri restano configurabili tramite dashboard.ini.
    managed = {
        key: value for key, value in automatic.items()
        if value is not None and str(value).strip()
    }
    result["repeater"].update(managed)
    result["repeater_managed_fields"] = list(managed)
    return jsonify(result)


@app.route("/api/status")
def api_status():
    repeater = read_repeater_info()

    repeater["rx_frequency_mhz"] = frequency_mhz(
        repeater["rx_frequency"]
    )
    repeater["tx_frequency_mhz"] = frequency_mhz(
        repeater["tx_frequency"]
    )

    return jsonify({
        "repeater": repeater,
        "system": system_info(),
        "agent": remote_agent.metadata() if remote_agent.enabled() else {"mode":"local", "online":True},
    })


@app.route("/api/heard")
def api_heard():
    history, active, fm = reconstruct()
    dmr_ids = get_dmr_ids()

    for event in history:
        if event.get("mode") not in ("DMR", "D-Star", "YSF"):
            continue

        callsign = str(event.get("callsign") or "").strip()

        if event.get("mode") == "DMR":
            radio_id = str(event.get("source") or "").strip()
            entry = dmr_ids.get(radio_id, {})
            if not callsign:
                callsign = entry.get("callsign", "")
                event["callsign"] = callsign

        event["name"] = get_operator_name(callsign)

    return jsonify(history)


@app.route("/api/live")
def api_live():
    history, active, fm = reconstruct()
    now = datetime.now(timezone.utc)
    timeout = 180

    candidates = []

    for (mode, slot), (started, payload) in active.items():
        start_dt = dt(started)
        if start_dt is None:
            continue

        elapsed = (now - start_dt).total_seconds()
        if elapsed < 0 or elapsed > timeout:
            continue

        direction = (
            "RF" if payload.get("source") == "rf"
            else "NET" if payload.get("source") == "network"
            else "?"
        )

        if mode == "DMR":
            callsign = str(payload.get("src_info") or "")
            source = str(payload.get("src_id") or "")
            destination = (
                ("TG " if payload.get("group") == "yes" else "")
                + str(payload.get("dst_id") or "")
            )
        else:
            callsign = str(payload.get("src_callsign") or "")
            source = callsign
            destination = str(
                payload.get("dst_callsign")
                or payload.get("reflector")
                or ""
            )

        candidate = {
            "active": True,
            "mode": mode,
            "callsign": callsign,
            "source": source,
            "name": "",
            "destination": destination,
            "direction": direction,
            "slot": slot,
            "elapsed": round(elapsed, 1),
            "_started": start_dt,
        }

        if mode == "D-Star":
            candidate["reflector"] = resolve_dstar_reflector_at(
                source=payload.get("source"),
                timestamp=started,
                reflector=payload.get("reflector"),
            )

        candidates.append(candidate)

    if fm is not None:
        start_dt = dt(fm)
        if start_dt is not None:
            elapsed = (now - start_dt).total_seconds()
            if 0 <= elapsed <= timeout:
                candidates.append({
                    "active": True,
                    "mode": "FM",
                    "callsign": "",
                    "source": "FM",
                    "name": "",
                    "destination": "",
                    "direction": "RF",
                    "slot": None,
                    "elapsed": round(elapsed, 1),
                    "_started": start_dt,
                })

    if not candidates:
        return jsonify({"active": False})

    latest = max(candidates, key=lambda x: x["_started"])
    latest.pop("_started", None)
    return jsonify(latest)


@app.route("/")
def index():
    return send_from_directory(FRONTEND_DIR, "index.html")


@app.route("/<path:path>")
def frontend(path):
    return send_from_directory(FRONTEND_DIR, path)


from multi import configure_multi
configure_multi(app)

if __name__ == "__main__":
    app.run(
        host="127.0.0.1",
        port=int(os.environ.get('MMDVM_DASHBOARD_PORT', '8089')),
        debug=False,
        threaded=True
    )
