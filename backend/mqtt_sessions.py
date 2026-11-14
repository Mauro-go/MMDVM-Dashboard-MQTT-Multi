from node_paths import data_file
from dstar_reflector import resolve_dstar_reflector_at
from contextlib import closing
#!/usr/bin/env python3
"""Read-only reconstruction of MQTT transmissions from events.sqlite3.

Prototype: DMR and D-Star completed calls; FM RF intervals are provisional.
Does not modify the existing collector, database schema or Flask service.
"""
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = data_file('events.sqlite3')


def dt(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except (ValueError, AttributeError):
        return None


def local_date_time(value):
    t = dt(value)
    if t is None:
        return '', ''
    local = t.astimezone()
    return local.strftime('%Y-%m-%d'), local.strftime('%H:%M:%S')


def read_events(db=DB):
    # Open read-only: safe to run while the collector writes.
    uri = Path(db).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=5)) as con:
        rows = con.execute('SELECT id,timestamp,mode,action,payload FROM events ORDER BY id').fetchall()
    for event_id, timestamp, mode, action, raw in rows:
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            yield event_id, timestamp, mode, action, payload


def reconstruct(db=DB):
    active = {}
    completed = []
    fm = None
    for event_id, timestamp, mode, action, p in read_events(db):
        if mode in ('DMR', 'D-Star', 'YSF', 'P25', 'NXDN', 'M17'):
            slot = p.get('slot') if mode == 'DMR' else None
            key = (mode, slot)
            if action == 'start' or (mode == 'YSF' and action == 'late_entry'):
                active[key] = (timestamp, p)
            elif action in ('end', 'lost'):
                start = active.pop(key, None)
                if start is None:
                    continue
                started, head = start
                direction = 'RF' if head.get('source') == 'rf' else 'NET' if head.get('source') == 'network' else '?'
                source = str(head.get('src_id') or head.get('src_callsign') or head.get('src_info') or '').strip()
                callsign = str(head.get('src_info') or '').strip() if mode == 'DMR' else str(head.get('src_callsign') or '').strip()
                if mode == 'D-Star' and head.get('src_ext'):
                    callsign += ' /' + str(head['src_ext']).strip().lstrip('/')
                if mode == 'DMR':
                    destination = ('TG ' if head.get('group') == 'yes' else '') + str(head.get('dst_id', ''))
                else:
                    destination = str(head.get('dst_callsign') or head.get('reflector') or '').strip()
                    if mode == 'YSF':
                        dg = head.get('dg-id')
                        reflector = str(head.get('reflector') or '').strip()
                        destination = ('DG-ID ' + str(dg) if dg is not None else '')
                        if reflector:
                            destination += (' @ ' if destination else '') + reflector
                reflector = str(head.get('reflector') or '').strip()
                if mode == 'D-Star':
                    reflector = resolve_dstar_reflector_at(
                        source=head.get('source'),
                        timestamp=started,
                        reflector=reflector,
                        db=db,
                    )
                rssi = p.get('rssi')
                rssi_avg = rssi.get('ave') if isinstance(rssi, dict) else None
                date, time = local_date_time(started)
                completed.append({
                    'date': date, 'time': time, 'mode': mode,
                    'direction': direction, 'source': source, 'callsign': callsign,
                    'name': '', 'destination': destination, 'slot': slot,
                    'reflector': reflector,
                    'duration': p.get('duration'), 'ber': p.get('ber'),
                    'rssi': rssi_avg, 'loss': p.get('loss'),
                    'type': mode.lower().replace('-', '') + ('_rf' if direction == 'RF' else '_net'),
                    'end_action': action, 'ysf_mode': head.get('mode') if mode == 'YSF' else None, '_timestamp': started,
                })
        elif mode == 'FM':
            state = p.get('state', action)
            if state == 'relaying_rf':
                if fm is None:
                    fm = timestamp
            elif state in ('relaying_wait_rf', 'hang', 'listening', 'timeout_rf', 'timeout_wait_rf'):
                if fm is not None:
                    start_dt, end_dt = dt(fm), dt(timestamp)
                    duration = max(0.0, (end_dt - start_dt).total_seconds()) if start_dt and end_dt else None
                    date, time = local_date_time(fm)
                    completed.append({
                        'date': date, 'time': time, 'mode': 'FM', 'direction': 'RF',
                        'source': 'FM', 'callsign': '', 'name': '', 'destination': '',
                        'slot': None, 'duration': duration, 'ber': None, 'rssi': None,
                        'loss': None, 'type': 'fm_rf', 'end_action': state,
                        '_timestamp': fm,
                    })
                    fm = None
    completed.sort(key=lambda x: x['_timestamp'], reverse=True)
    for item in completed:
        item.pop('_timestamp', None)
    return completed, active, fm


if __name__ == '__main__':
    history, active, fm = reconstruct()
    print('Completed sessions:', len(history))
    print('Open digital sessions:', len(active))
    print('Open FM RF interval:', bool(fm))
    for item in history[:15]:
        print(json.dumps(item, ensure_ascii=False))
