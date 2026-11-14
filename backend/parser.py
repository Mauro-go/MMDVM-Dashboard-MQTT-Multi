#!/usr/bin/env python3

import re
import glob
import csv
import configparser
import config as dashboard_config
import remote_agent  # PARSER_REMOTE_GUARD_V1
from datetime import datetime, timezone, timedelta

LOG_GLOB = dashboard_config.get_path("mmdvm_logs")
MMDVM_INI = dashboard_config.get_mmdvm_ini()
DMR_IDS_FILE = dashboard_config.get_path("dmr_ids")
TG_LIST_FILE = dashboard_config.get_tg_list()
HOURS_HISTORY = 24


def get_repeater_callsign():
    if remote_agent.enabled():
        return str(remote_agent.repeater_info().get("callsign") or "").strip()
    try:
        config = configparser.ConfigParser(strict=False)
        config.optionxform = str
        config.read(dashboard_config.get_mmdvm_ini())
        return config.get("General", "Callsign", fallback="").strip()
    except Exception:
        return ""



# PARSER_AGENT_RADIO_V1
def get_fm_ctcss():
    if remote_agent.enabled():
        settings = remote_agent.repeater_settings()
        return str(settings.get("ctcss") or "").strip() or None

    try:
        cfg = configparser.ConfigParser(strict=False)
        cfg.read(dashboard_config.get_mmdvm_ini())
        for key in ("CTCSSFrequency", "CTCSS"):
            if cfg.has_option("FM", key):
                return cfg.get("FM", key)
    except (OSError, configparser.Error):
        pass
    return None

REPEATER_CALLSIGN = get_repeater_callsign()


def load_dmr_ids():
    database = {}

    try:
        with open(dashboard_config.get_path("dmr_ids"), "r", errors="replace") as f:
            for line in f:
                parts = line.strip().split(None, 2)

                if len(parts) < 2:
                    continue

                radio_id = parts[0]

                if not radio_id.isdigit():
                    continue

                database[radio_id] = {
                    "callsign": parts[1],
                    "name": parts[2] if len(parts) > 2 else ""
                }

    except OSError:
        pass

    return database


DMR_IDS = load_dmr_ids()

# Memorizza subito la firma del database già caricato.
# Evita una seconda lettura completa alla prima richiesta.
from pathlib import Path as _Path

_DMR_IDS_PATH = dashboard_config.get_path("dmr_ids")
try:
    _DMR_IDS_STAT = _Path(_DMR_IDS_PATH).stat()
    _DMR_IDS_SIGNATURE = (
        _DMR_IDS_PATH,
        _DMR_IDS_STAT.st_mtime_ns,
        _DMR_IDS_STAT.st_size
    )
except OSError:
    _DMR_IDS_SIGNATURE = (_DMR_IDS_PATH, None, None)


def get_dmr_ids():
    """Aggiorna il database DMR quando cambia il file."""
    global DMR_IDS, _DMR_IDS_SIGNATURE

    path = dashboard_config.get_path("dmr_ids")

    try:
        from pathlib import Path
        st = Path(path).stat()
        signature = (path, st.st_mtime_ns, st.st_size)
    except OSError:
        signature = (path, None, None)

    if signature != _DMR_IDS_SIGNATURE:
        DMR_IDS = load_dmr_ids()
        _DMR_IDS_SIGNATURE = signature

    return DMR_IDS


# Indice nominativo -> primo nome, ricostruito solo quando
# cambia il database DMRIds.dat.
_CALLSIGN_NAMES = {}
_CALLSIGN_NAMES_SOURCE = None


def get_callsign_names():
    global _CALLSIGN_NAMES, _CALLSIGN_NAMES_SOURCE

    ids = get_dmr_ids()

    if _CALLSIGN_NAMES_SOURCE is not ids:
        names = {}

        for entry in ids.values():
            callsign = str(entry.get("callsign") or "").strip().upper()
            full_name = str(entry.get("name") or "").strip()

            if callsign and full_name and callsign not in names:
                names[callsign] = full_name.split()[0]

        _CALLSIGN_NAMES = names
        _CALLSIGN_NAMES_SOURCE = ids

    return _CALLSIGN_NAMES


def get_operator_name(callsign):
    callsign = str(callsign or "").strip().upper().split("/")[0].strip()
    return get_callsign_names().get(callsign, "")


PATTERNS = [
    # DMR RF
    (
        "dmr_rf",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"DMR Slot (?P<slot>[12]), received RF end of voice transmission "
            r"from (?P<source>\d+) to (?P<dest>TG \d+|\d+), "
            r"(?P<duration>[\d.]+) seconds, "
            r"(?:BER: (?P<ber>[\d.]+)%, )?"
            r"RSSI: (?P<rssi>.+?) dBm$"
        )
    ),

    # DMR NETWORK
    (
        "dmr_net",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"DMR Slot (?P<slot>[12]), received network end of voice transmission "
            r"from (?P<source>\d+) to (?P<dest>TG \d+|\d+), "
            r"(?P<duration>[\d.]+) seconds, "
            r"(?P<loss>[\d.]+)% packet loss, "
            r"BER: (?P<ber>[\d.]+)%$"
        )
    ),

    # YSF RF - fine trasmissione
    (
        "ysf_rf",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"YSF, received RF end of transmission from "
            r"(?P<source>.+?)\s+to (?P<dest>DG-ID \d+), "
            r"(?P<duration>[\d.]+) seconds, "
            r"(?:BER: (?P<ber>[\d.]+)%, )?"
            r"RSSI: (?P<rssi>.+?) dBm$"
        )
    ),

    # YSF NETWORK
    (
        "ysf_net",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"YSF, received network end of transmission from "
            r"(?P<source>.+?)\s+to (?P<dest>DG-ID \d+), "
            r"(?P<duration>[\d.]+) seconds, "
            r"(?P<loss>[\d.]+)% packet loss, "
            r"BER: (?P<ber>[\d.]+)%$"
        )
    ),

    # D-STAR RF
    (
        "dstar_rf",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"D-Star, received RF end of transmission from "
            r"(?P<source>.+?) to (?P<dest>.+?)\s*, "
            r"(?P<duration>[\d.]+) seconds, "
            r"BER: (?P<ber>[\d.]+)%, "
            r"RSSI: (?P<rssi>.+?) dBm$"
        )
    ),

    # D-STAR NETWORK
    (
        "dstar_net",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"D-Star, received network end of transmission from "
            r"(?P<source>.+?) to (?P<dest>.+?)\s*, "
            r"(?P<duration>[\d.]+) seconds, "
            r"(?P<loss>[\d.]+)% packet loss, "
            r"BER: (?P<ber>[\d.]+)%$"
        )
    ),
]


def parse_line(line):
    line = line.strip()

    for event_type, regex in PATTERNS:
        match = regex.match(line)

        if not match:
            continue

        data = match.groupdict()

        event = {
            "type": event_type,
            "date": data.get("date"),
            "time": data.get("time"),
            "mode": (
                "DMR" if event_type.startswith("dmr")
                else "YSF" if event_type.startswith("ysf")
                else "D-Star"
            ),
            "direction": "RF" if event_type.endswith("_rf") else "NET",
            "source": data.get("source", "").strip(),
            "destination": data.get("dest", "").strip(),
            "duration": float(data["duration"]) if data.get("duration") else None,
            "ber": (
                None if event_type == "ysf_net"
                else float(data["ber"]) if data.get("ber") else None
            ),
            "loss": float(data["loss"]) if data.get("loss") else None,
            "rssi": data.get("rssi"),
            "slot": int(data["slot"]) if data.get("slot") else None,
            "callsign": "",
            "name": "",
        }

        if event["mode"] == "DMR":
            dmr_user = get_dmr_ids().get(event["source"])

            if dmr_user:
                event["callsign"] = dmr_user["callsign"]
                event["name"] = dmr_user["name"]

        return event

    return None


def load_tg_labels():
    labels = {}

    try:
        with open(dashboard_config.get_tg_list(), "r", errors="replace", newline="") as f:
            reader = csv.DictReader(f)

            for row in reader:
                if row.get("MODE", "").strip().upper() != "DMR":
                    continue

                ts = row.get("TS", "").strip()
                tg = row.get("TG", "").strip()
                label = row.get("LABEL", "").strip()

                if ts and tg and label:
                    labels[(ts, tg)] = label

    except OSError:
        pass

    return labels


def load_recent():
    tg_labels = load_tg_labels()
    files = sorted(glob.glob(dashboard_config.get_path("mmdvm_logs")))

    if not files:
        return []

    found = []

    # Stato temporaneo per YSF.
    # Alcune trasmissioni YSF non producono una riga
    # "end of transmission": in quel caso il QSO viene
    # ricostruito da "received RF header" fino a "Mode set to Idle".
    ysf_current = None

    # Stato temporaneo per YSF NET.
    # La riga iniziale contiene anche il ponte/nodo di ingresso:
    # "received network data from IV3JDV ... at IR3UIC"
    # mentre la riga END contiene durata/loss ma non il ponte.
    ysf_net_bridge = {}

    # Il log MMDVMHost usa UTC.
    # Per una finestra di 24 ore bastano normalmente gli ultimi 2 file,
    # ma ne leggiamo 3 per sicurezza a cavallo dei giorni.
    for filename in reversed(files[-3:]):
        try:
            with open(filename, "r", errors="replace") as f:
                for line in f:
                    # Apertura di una sessione YSF RF.
                    ysf_header = re.match(
                        r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
                        r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
                        r"YSF, received RF header from "
                        r"(?P<source>.+?)\s+to (?P<dest>DG-ID \d+)$",
                        line.strip()
                    )

                    if ysf_header:
                        data = ysf_header.groupdict()

                        try:
                            dt = datetime.strptime(
                                data["date"] + " " + data["time"],
                                "%Y-%m-%d %H:%M:%S"
                            ).replace(tzinfo=timezone.utc)

                            ysf_current = {
                                "date": data["date"],
                                "time": data["time"],
                                "source": data["source"].strip(),
                                "destination": data["dest"].strip(),
                                "_timestamp": dt.timestamp(),
                            }
                        except ValueError:
                            ysf_current = None

                    # YSF RF terminato con "transmission lost".
                    # MMDVMHost fornisce comunque durata, BER e RSSI.
                    # Manteniamo la destinazione DG-ID presa dall'header,
                    # perche' la riga LOST riporta genericamente "to ALL".
                    ysf_lost = re.match(
                        r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
                        r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
                        r"YSF, transmission lost from "
                        r"(?P<source>.+?)\s+to .+?, "
                        r"(?P<duration>[\d.]+) seconds, "
                        r"BER: (?P<ber>[\d.]+)%, "
                        r"RSSI: (?P<rssi>.+?) dBm$",
                        line.strip()
                    )

                    if ysf_current and ysf_lost:
                        lost = ysf_lost.groupdict()

                        # Associa il LOST solo alla sessione RF corrente.
                        if lost["source"].strip() == ysf_current["source"]:
                            dt_local = datetime.fromtimestamp(
                                ysf_current["_timestamp"],
                                tz=timezone.utc
                            ).astimezone()

                            found.append({
                                "type": "ysf_rf",
                                "date": dt_local.strftime("%Y-%m-%d"),
                                "time": dt_local.strftime("%H:%M:%S"),
                                "mode": "YSF",
                                "direction": "RF",
                                "source": ysf_current["source"],
                                "destination": ysf_current["destination"],
                                "duration": float(lost["duration"]),
                                "ber": float(lost["ber"]),
                                "loss": None,
                                "rssi": lost["rssi"].strip(),
                                "slot": None,
                                "callsign": "",
                                "name": "",
                                "_timestamp": ysf_current["_timestamp"],
                            })

                            # La sessione e' gia' stata registrata:
                            # impedisce al successivo Idle di crearne una seconda.
                            ysf_current = None

                    # Un vero END YSF verra' gestito normalmente da
                    # parse_line(): disabilitiamo quindi il fallback.
                    if (
                        ysf_current
                        and "YSF, received RF end of transmission " in line
                    ):
                        ysf_current = None

                    # Se MMDVMHost torna Idle senza avere prodotto un END,
                    # costruiamo una voce Last Heard dalla sessione YSF.
                    if ysf_current and "Mode set to Idle" in line:
                        idle_match = re.match(
                            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
                            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
                            r"Mode set to Idle$",
                            line.strip()
                        )

                        if idle_match:
                            try:
                                idle_dt = datetime.strptime(
                                    idle_match.group("date") + " " +
                                    idle_match.group("time"),
                                    "%Y-%m-%d %H:%M:%S"
                                ).replace(tzinfo=timezone.utc)

                                duration = (
                                    idle_dt.timestamp() -
                                    ysf_current["_timestamp"]
                                )

                                if 0.0 < duration <= 240.0:
                                    dt_local = datetime.fromtimestamp(
                                        ysf_current["_timestamp"],
                                        tz=timezone.utc
                                    ).astimezone()

                                    found.append({
                                        "type": "ysf_rf",
                                        "date": dt_local.strftime("%Y-%m-%d"),
                                        "time": dt_local.strftime("%H:%M:%S"),
                                        "mode": "YSF",
                                        "direction": "RF",
                                        "source": ysf_current["source"],
                                        "destination": ysf_current["destination"],
                                        # Nessun vero END YSF: la durata non e'
                                        # disponibile. Idle include il mode hang timer.
                                        "duration": None,
                                        "ber": None,
                                        "loss": None,
                                        "rssi": None,
                                        "slot": None,
                                        "callsign": "",
                                        "name": "",
                                        "_timestamp": ysf_current["_timestamp"],
                                    })

                            except ValueError:
                                pass

                        ysf_current = None

                    # Memorizza il ponte/nodo di ingresso delle sessioni YSF NET.
                    ysf_net_header = re.match(
                        r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
                        r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
                        r"YSF, received network data from "
                        r"(?P<source>.+?)\s+to (?P<dest>DG-ID \d+)"
                        r"(?:\s+at\s+(?P<bridge>\S+))?$",
                        line.strip()
                    )

                    if ysf_net_header:
                        net_data = ysf_net_header.groupdict()
                        source = net_data["source"].strip()
                        bridge = (net_data.get("bridge") or "").strip()

                        if bridge:
                            ysf_net_bridge[source] = bridge
                        else:
                            ysf_net_bridge.pop(source, None)

                    event = parse_line(line)

                    if not event:
                        continue

                    # Per YSF NET aggiunge il ponte solo se realmente presente
                    # nella relativa riga iniziale.
                    if event["type"] == "ysf_net":
                        source = event["source"]
                        bridge = ysf_net_bridge.pop(source, None)

                        if bridge:
                            event["source"] = source + "/" + bridge

                    try:
                        # Timestamp presente nel log MMDVMHost = UTC
                        dt_utc = datetime.strptime(
                            event["date"] + " " + event["time"],
                            "%Y-%m-%d %H:%M:%S"
                        ).replace(tzinfo=timezone.utc)

                        # Converte usando automaticamente il timezone
                        # configurato nel sistema Linux (Europe/Rome).
                        dt_local = dt_utc.astimezone()

                        event["date"] = dt_local.strftime("%Y-%m-%d")
                        event["time"] = dt_local.strftime("%H:%M:%S")
                        event["_timestamp"] = dt_utc.timestamp()

                    except ValueError:
                        continue

                    if event["mode"] == "DMR":
                        tg = event["destination"].replace("TG ", "").strip()
                        ts = str(event["slot"]) if event["slot"] is not None else ""

                        event["destination"] = tg_labels.get(
                            (ts, tg),
                            tg
                        )

                    found.append(event)

        except OSError:
            continue

    # Mostra soltanto i collegamenti della giornata corrente.
    # La mezzanotte viene calcolata nel timezone locale del sistema
    # (Europe/Rome) e poi confrontata con i timestamp UTC dei log.
    now_local = datetime.now().astimezone()
    midnight_local = now_local.replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0
    )
    cutoff_ts = midnight_local.timestamp()

    found = [
        event for event in found
        if event.get("_timestamp", 0) >= cutoff_ts
    ]

    found.sort(
        key=lambda x: x["_timestamp"],
        reverse=True
    )

    # Campo interno: non serve nell'API.
    for event in found:
        event.pop("_timestamp", None)

    return found


if __name__ == "__main__":
    recent = load_recent()

    print()
    print("MMDVM Dashboard - Parser test")
    print("Repeater:", REPEATER_CALLSIGN or "UNKNOWN")
    print("=" * 100)

    for e in recent:
        extra = ""

        if e["slot"]:
            extra += " TS{}".format(e["slot"])

        if e["rssi"]:
            extra += " RSSI {}".format(e["rssi"])

        if e["loss"] is not None:
            extra += " LOSS {}%".format(e["loss"])

        print(
            "{date} {time}  {mode:<7} {direction:<3} "
            "{source:<18} -> {destination:<12} "
            "{duration:>5.1f}s BER {ber:>4.1f}%{extra}".format(
                extra=extra,
                **e
            )
        )

    print()
    print("Eventi trovati:", len(recent))

# ----------------------------------------------------------------------
# LIVE QSO
# ----------------------------------------------------------------------

LIVE_MAX_AGE = 240  # sicurezza: nessun LIVE puo' restare aperto oltre 4 minuti

LIVE_HEADER_PATTERNS = [
    (
        "dmr_rf",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"DMR Slot (?P<slot>[12]), received RF voice header "
            r"from (?P<source>\d+) to (?P<dest>TG \d+|\d+)"
        )
    ),
    (
        "dmr_net",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"DMR Slot (?P<slot>[12]), received network voice header "
            r"from (?P<source>\d+) to (?P<dest>TG \d+|\d+)"
        )
    ),
    (
        "ysf_net",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"YSF, received network data from "
            r"(?P<source>.+?)\s+to (?P<dest>DG-ID \d+)"
            r"(?:\s+at\s+(?P<bridge>\S+))?$"
        )
    ),
    (
        "ysf_rf",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"YSF, received RF header from "
            r"(?P<source>.+?)\s+to (?P<dest>DG-ID \d+)$"
        )
    ),
    (
        "ysf_rf",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"YSF, received RF late entry from "
            r"(?P<source>.+?)\s+to (?P<dest>DG-ID \d+)$"
        )
    ),
    (
        "dstar_rf",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"D-Star, received RF header from "
            r"(?P<source>.+?) to (?P<dest>.+?)(?:\s+via\s+.+)?$"
        )
    ),
    (
        "dstar_net",
        re.compile(
            r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
            r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
            r"D-Star, received network header from "
            r"(?P<source>.+?) to (?P<dest>.+?)(?:\s+via\s+.+)?$"
        )
    ),
]


def parse_live_header(line):
    line = line.strip()

    for event_type, regex in LIVE_HEADER_PATTERNS:
        match = regex.match(line)

        if not match:
            continue

        data = match.groupdict()

        source = data.get("source", "").strip()

        # YSF NET: se MMDVMHost indica il ponte/nodo di ingresso,
        # visualizza CALLSIGN/PONTE (es. IV3JDV/IR3UIC).
        if event_type == "ysf_net":
            bridge = (data.get("bridge") or "").strip()
            if bridge:
                source = source + "/" + bridge

        event = {
            "active": True,
            "mode": (
                "DMR" if event_type.startswith("dmr")
                else "YSF" if event_type.startswith("ysf")
                else "D-Star"
            ),
            "direction": "RF" if event_type.endswith("_rf") else "NET",
            "source": source,
            "destination": data.get("dest", "").strip(),
            "slot": int(data["slot"]) if data.get("slot") else None,
            "callsign": "",
            "name": "",
            "date": data["date"],
            "time": data["time"],
        }

        if event["mode"] == "DMR":
            dmr_user = get_dmr_ids().get(event["source"])
            if dmr_user:
                event["callsign"] = dmr_user["callsign"]
                event["name"] = dmr_user["name"]

        try:
            dt_utc = datetime.strptime(
                event["date"] + " " + event["time"],
                "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)

            dt_local = dt_utc.astimezone()

            event["date"] = dt_local.strftime("%Y-%m-%d")
            event["time"] = dt_local.strftime("%H:%M:%S")
            event["_timestamp"] = dt_utc.timestamp()

        except ValueError:
            return None

        return event

    # DMR RF late entry:
    # MMDVMHost ha riagganciato una trasmissione già in corso
    match = re.match(
        r"^M:\s+(?P<date>\d{4}-\d{2}-\d{2})\s+"
        r"(?P<time>\d{2}:\d{2}:\d{2})\.\d+\s+"
        r"DMR Slot (?P<slot>[12]), received RF late entry "
        r"from (?P<source>\d+) to (?P<dest>TG \d+|\d+)",
        line
    )

    if match:
        data = match.groupdict()

        event = {
            "active": True,
            "mode": "DMR",
            "direction": "RF",
            "source": data["source"],
            "destination": data["dest"],
            "slot": int(data["slot"]),
            "callsign": "",
            "name": "",
            "date": data["date"],
            "time": data["time"],
        }

        dmr_user = get_dmr_ids().get(event["source"])
        if dmr_user:
            event["callsign"] = dmr_user["callsign"]
            event["name"] = dmr_user["name"]

        try:
            dt_utc = datetime.strptime(
                event["date"] + " " + event["time"],
                "%Y-%m-%d %H:%M:%S"
            ).replace(tzinfo=timezone.utc)

            dt_local = dt_utc.astimezone()
            event["date"] = dt_local.strftime("%Y-%m-%d")
            event["time"] = dt_local.strftime("%H:%M:%S")
            event["_timestamp"] = dt_utc.timestamp()

        except ValueError:
            return None

        return event

    return None


def live_event_closed(line, event):
    line = line.strip()

    if event["mode"] == "DMR":
        slot_text = "DMR Slot {},".format(event["slot"])

        if slot_text not in line:
            return False

        if "end of voice transmission" in line:
            return True

        if "RF voice transmission lost" in line:
            return True

        if "watchdog has expired" in line:
            return True

    elif event["mode"] == "D-Star":
        if not line.startswith("M:"):
            return False

        if "D-Star, received " in line and " end of transmission " in line:
            return True

    elif event["mode"] == "YSF":
        if not line.startswith("M:"):
            return False

        # In YSF "transmission lost" puo' essere seguito da un late entry.
        # Chiudiamo quindi il LIVE solo alla vera fine della trasmissione.
        if (
            "YSF, received RF end of transmission " in line
            or "YSF, received network end of transmission " in line
        ):
            return True

    return False


def load_live():
    files = sorted(glob.glob(dashboard_config.get_path("mmdvm_logs")))

    if not files:
        return {"active": False}

    filename = files[-1]

    try:
        with open(filename, "r", errors="replace") as f:
            # I log giornalieri sono piccoli: per il LIVE leggiamo soltanto
            # le ultime 500 righe.
            lines = f.readlines()[-500:]
    except OSError:
        return {"active": False}

    current = None
    fm_start = None
    ysf_lost_timestamp = None
    YSF_LOST_GRACE = 8.0

    for line in lines:

        # Stato FM analogico.
        # Non viene aggiunto al Last Heard: serve esclusivamente al LIVE.
        fm = re.search(
            r'^M:\s+(\d{4}-\d{2}-\d{2})\s+'
            r'(\d{2}:\d{2}:\d{2}\.\d+)\s+Mode set to FM',
            line
        )

        if fm:
            dt = datetime.strptime(
                f"{fm.group(1)} {fm.group(2)}",
                "%Y-%m-%d %H:%M:%S.%f"
            ).replace(tzinfo=timezone.utc)

            fm_start = dt
            current = None
            ysf_lost_timestamp = None
            continue

        if "Mode set to Idle" in line:
            fm_start = None
            current = None
            ysf_lost_timestamp = None
            continue

        header = parse_live_header(line)

        if header:
            # Un late entry YSF appartiene alla sessione gia' aperta:
            # conserva timestamp iniziale e quindi anche il contatore.
            if (
                current
                and current.get("mode") == "YSF"
                and header.get("mode") == "YSF"
                and current.get("source") == header.get("source")
                and "received RF late entry" in line
            ):
                current["destination"] = header["destination"]
                ysf_lost_timestamp = None
            else:
                current = header
                ysf_lost_timestamp = None

            continue

        # YSF transmission lost: non chiude immediatamente.
        # Memorizziamo quando e' avvenuto e concediamo tempo
        # a MMDVMHost per un eventuale late entry.
        if (
            current
            and current.get("mode") == "YSF"
            and "YSF, transmission lost " in line
        ):
            m_lost = re.match(
                r"^M:\s+(\d{4}-\d{2}-\d{2})\s+"
                r"(\d{2}:\d{2}:\d{2})\.\d+",
                line.strip()
            )

            if m_lost:
                try:
                    dt_lost = datetime.strptime(
                        m_lost.group(1) + " " + m_lost.group(2),
                        "%Y-%m-%d %H:%M:%S"
                    ).replace(tzinfo=timezone.utc)

                    ysf_lost_timestamp = dt_lost.timestamp()
                except ValueError:
                    pass

            continue

        if current and live_event_closed(line, current):
            current = None
            ysf_lost_timestamp = None

    if fm_start is not None:
        age = datetime.now(timezone.utc).timestamp() - fm_start.timestamp()

        if 0 <= age <= LIVE_MAX_AGE:
            ctcss = get_fm_ctcss()

            event = {
                "active": True,
                "mode": "FM",
                "direction": "RF",
                "source": "FM ANALOGICO",
                "destination": "",
                "slot": None,
                "callsign": None,
                "name": None,
                "date": fm_start.astimezone().strftime("%Y-%m-%d"),
                "time": fm_start.astimezone().strftime("%H:%M:%S"),
                "elapsed": round(age, 1)
            }

            if ctcss:
                event["ctcss"] = ctcss

            return event

    if not current:
        return {"active": False}

    # Se YSF e' terminato con transmission lost e non e' arrivato
    # alcun late entry entro il periodo di tolleranza, chiude il LIVE.
    if current.get("mode") == "YSF" and ysf_lost_timestamp is not None:
        lost_age = datetime.now(timezone.utc).timestamp() - ysf_lost_timestamp

        if lost_age >= YSF_LOST_GRACE:
            return {"active": False}

    age = datetime.now(timezone.utc).timestamp() - current["_timestamp"]

    if age < 0 or age > LIVE_MAX_AGE:
        return {"active": False}

    current["elapsed"] = round(age, 1)
    current.pop("_timestamp", None)

    return current

# ----------------------------------------------------------------------
# FM ANALOG LIVE
# ----------------------------------------------------------------------

def load_fm_live():
    """Rileva una sessione FM analogica attualmente attiva dal log MMDVMHost."""

    files = sorted(glob.glob(dashboard_config.get_path("mmdvm_logs")))
    if not files:
        return None

    logfile = files[-1]

    try:
        with open(logfile, "r", errors="ignore") as f:
            lines = f.readlines()[-500:]
    except Exception:
        return None

    fm_start = None

    pattern = re.compile(
        r'^M:\s+(\d{4}-\d{2}-\d{2})\s+'
        r'(\d{2}:\d{2}:\d{2}\.\d+)\s+Mode set to (FM|Idle)'
    )

    for line in lines:
        m = pattern.search(line)
        if not m:
            continue

        date_s, time_s, mode = m.groups()

        try:
            dt = datetime.strptime(
                f"{date_s} {time_s}",
                "%Y-%m-%d %H:%M:%S.%f"
            ).replace(tzinfo=timezone.utc).astimezone()
        except Exception:
            continue

        if mode == "FM":
            fm_start = dt

        elif mode == "Idle" and fm_start is not None:
            fm_start = None

    if fm_start is None:
        return None

    now = datetime.now().astimezone()
    elapsed = (now - fm_start).total_seconds()

    if elapsed < 0 or elapsed > LIVE_MAX_AGE:
        return None

    ctcss = get_fm_ctcss()

    event = {
        "active": True,
        "mode": "FM",
        "direction": "RF",
        "source": "FM ANALOGICO",
        "destination": "",
        "slot": None,
        "callsign": None,
        "name": None,
        "date": fm_start.strftime("%Y-%m-%d"),
        "time": fm_start.strftime("%H:%M:%S"),
        "elapsed": round(elapsed, 1)
    }

    if ctcss:
        event["ctcss"] = ctcss

    return event
