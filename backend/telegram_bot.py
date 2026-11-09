#!/usr/bin/env python3
"""MMDVM MQTT -> Telegram notifications. Python 3.9+."""
import configparser
import datetime as dt
import html
import json
import logging
import os
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from mqtt_settings import load as mqtt_load
from telegram_settings import load as telegram_load

LOG = logging.getLogger('mmdvm-telegram')
from config import get_mmdvm_ini


def repeater_callsign():
    # TELEGRAM_REMOTE_CALLSIGN_V1
    import config as dashboard_config
    import remote_agent

    if remote_agent.enabled():
        call = str(
            remote_agent.repeater_info().get("callsign") or ""
        ).strip().upper()
        if call and call != "MMDVM":
            return call

        call = dashboard_config.get(
            "repeater", "callsign", ""
        ).strip().upper()
        if call:
            return call

        raise RuntimeError(
            "Missing repeater callsign in remote Agent configuration"
        )

    name = get_mmdvm_ini()
    cp = configparser.ConfigParser(interpolation=None, strict=False)

    try:
        with open(name, encoding='utf-8') as f:
            cp.read_file(f)
        call = cp.get('General', 'Callsign', fallback='').strip().upper()
        if call:
            return call
    except (OSError, configparser.Error) as exc:
        LOG.error('Cannot read configured MMDVM.ini at %s: %s', name, exc)

    raise RuntimeError(
        'Missing [General] Callsign in configured MMDVM.ini: ' + name
    )


def utc_now():
    return dt.datetime.now(dt.timezone.utc)


def event_time(event):
    value = event.get('timestamp')
    if isinstance(value, str):
        try:
            parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is not None:
                return parsed.astimezone(dt.timezone.utc)
        except ValueError:
            pass
    return utc_now()


def telegram_send(token, chat_id, message):
    payload = urllib.parse.urlencode({'chat_id': chat_id, 'text': message, 'parse_mode': 'HTML', 'disable_web_page_preview': 'true'}).encode()
    request = urllib.request.Request('https://api.telegram.org/bot' + token + '/sendMessage', data=payload, method='POST')
    with urllib.request.urlopen(request, timeout=12) as response:
        result = json.load(response)
    if not result.get('ok'):
        raise RuntimeError('Telegram API returned failure')


class Bot:
    def __init__(self):
        self.mqtt = mqtt_load()
        self.callsign = repeater_callsign()
        self.starts = {}
        self.last_notification = 0.0
        self.client = None
        LOG.info('Repeater=%s; Telegram notifications active when enabled in Admin', self.callsign)

    def on_connect(self, client, userdata, flags, rc, *extra):
        if rc != 0:
            LOG.error('MQTT connection rejected: %s', rc)
            return
        self.starts.clear()
        topic = self.mqtt.get('topic') or 'mmdvm/+/json'
        client.subscribe(topic, qos=0)
        LOG.info('MQTT connected; subscribed=%s', topic)

    def on_disconnect(self, client, userdata, rc, *extra):
        self.starts.clear()
        LOG.warning('MQTT disconnected (%s)', rc)

    def on_message(self, client, userdata, msg):
        try:
            event = json.loads(msg.payload.decode('utf-8'))
            if not isinstance(event, dict):
                return

            supported = ('DMR', 'D-Star', 'YSF', 'P25', 'NXDN', 'FM', 'M17')

            for mode, payload in event.items():
                if mode not in supported or not isinstance(payload, dict):
                    continue

                if mode == 'FM':
                    self.process_fm(payload)
                    continue

                action = str(
                    payload.get('action') or payload.get('state') or ''
                ).lower()

                if action not in ('start', 'end', 'lost'):
                    continue

                slot = str(payload.get('slot', '')) if mode == 'DMR' else ''
                key = (mode, slot)

                if action == 'start':
                    self.starts[key] = (
                        payload, time.monotonic(), event_time(payload)
                    )
                    continue

                start = self.starts.pop(key, None)
                if not start:
                    LOG.info('SKIP %s %s without matching start', mode, action)
                    continue

                begin, tick, started_at = start
                if time.monotonic() - tick > 3600:
                    LOG.info('SKIP stale %s start', mode)
                    continue

                self.process(mode, begin, payload, started_at)

        except Exception:
            LOG.exception('Failed to process MQTT message')

    def process_fm(self, payload):
        state = str(payload.get('state') or payload.get('action') or '').lower()
        key = ('FM', '')

        if state == 'relaying_rf':
            if key not in self.starts:
                self.starts[key] = (
                    {'source': 'rf'}, time.monotonic(), event_time(payload)
                )
            return

        if state == 'relaying_wait_rf':
            start = self.starts.pop(key, None)
            if not start:
                return

            begin, tick, started_at = start
            duration = max(
                0.0,
                (event_time(payload) - started_at).total_seconds()
            )

            if duration > 3600:
                return

            self.process(
                'FM',
                begin,
                {'duration': duration},
                started_at
            )

        elif state == 'hang':
            self.starts.pop(key, None)

    def process(self, mode, begin, end, started_at):
        settings = telegram_load()
        if not settings.get('enabled'):
            LOG.info('SKIP disabled')
            return

        if mode not in settings.get('modes', []):
            LOG.info('SKIP %s disabled mode', mode)
            return

        try:
            duration = float(end.get('duration', 0))
        except (TypeError, ValueError):
            duration = 0.0

        if duration <= float(settings.get('min_duration', 2)):
            LOG.info('SKIP %s short duration %.2fs', mode, duration)
            return

        source = str(begin.get('source', '')).lower()
        if source not in settings.get('sources', ['rf', 'network']):
            LOG.info('SKIP %s source %s', mode, source)
            return

        talkgroup = None

        if mode == 'DMR':
            caller = str(
                begin.get('src_info') or begin.get('src_id') or ''
            ).strip().upper()

            talkgroup = begin.get('dst_id')

            if not caller:
                LOG.info('SKIP DMR missing caller')
                return

            try:
                tg = int(talkgroup)
            except (ValueError, TypeError):
                LOG.info('SKIP DMR missing TG')
                return

            excluded = settings.get('tg_exclude', [])
            included = settings.get('tg_include', [])

            if tg in excluded or (
                settings.get('tg_policy') == 'only_included'
                and tg not in included
            ):
                LOG.info('SKIP DMR TG filter: %s', tg)
                return

        elif mode == 'FM':
            caller = ''

        else:
            caller = str(
                begin.get('src_callsign')
                or begin.get('src_info')
                or begin.get('src_id')
                or ''
            ).strip().upper()

            if mode == 'D-Star':
                ext = str(begin.get('src_ext') or '').strip().upper()

                if settings.get('exclude_time', True) and ext == 'TIME':
                    LOG.info('SKIP D-Star time signal')
                    return

                if settings.get('exclude_beacons', True) and caller == self.callsign:
                    LOG.info('SKIP D-Star own repeater beacon')
                    return

            if not caller:
                LOG.info('SKIP %s missing caller', mode)
                return

        if caller and caller in [
            x.upper() for x in settings.get('callsign_exclude', [])
        ]:
            LOG.info('SKIP excluded callsign %s', caller)
            return

        gap = float(settings.get('notification_gap', 10))
        if time.monotonic() - self.last_notification < gap:
            LOG.info('SKIP rate limit')
            return

        title = '📡 ' + html.escape(self.callsign) + ' – Attività radio'

        fields = [
            f'<b>{title}</b>',
            f'Modo: <b>{html.escape(mode)}</b>'
        ]

        if caller:
            fields.append(
                f'Nominativo: <b>{html.escape(caller)}</b>'
            )

        if mode == 'DMR':
            fields.extend([
                f'DMR ID: {html.escape(str(begin.get("src_id", "")))}',
                f'TG: <b>{html.escape(str(talkgroup))}</b>',
                f'TS: {html.escape(str(begin.get("slot", "")))}'
            ])

        elif mode not in ('FM',):
            reflector = str(begin.get('reflector') or '')
            if reflector:
                fields.append(
                    'Reflector: ' + html.escape(reflector)
                )

        fields.extend([
            f'Origine: {html.escape(source)}',
            f'Durata: {duration:.1f} s',
            'UTC: ' + started_at.strftime('%Y-%m-%d %H:%M:%S')
        ])

        message = '\n'.join(fields)

        token = settings.get('bot_token', '')
        chat_id = settings.get('chat_id', '')

        if not token or not chat_id:
            LOG.warning('SKIP Telegram token/chat missing')
            return

        try:
            telegram_send(token, chat_id, message)
            self.last_notification = time.monotonic()
            LOG.info('SENT %s %s', mode, caller or 'RF')
        except (urllib.error.URLError, ValueError, RuntimeError) as exc:
            LOG.error('Telegram send failed: %s', type(exc).__name__)

    def run(self):
        try:
            import paho.mqtt.client as mqtt
        except ImportError as exc:
            raise RuntimeError('Missing dependency: python3-paho-mqtt') from exc
        try:
            client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION1)
        except (AttributeError, TypeError):
            client = mqtt.Client()
        self.client = client
        client.on_connect = self.on_connect
        client.on_disconnect = self.on_disconnect
        client.on_message = self.on_message
        username = self.mqtt.get('username')
        if username:
            client.username_pw_set(username, self.mqtt.get('password') or None)
        if self.mqtt.get('tls'):
            client.tls_set()
        client.connect(str(self.mqtt['host']), int(self.mqtt['port']), keepalive=60)
        client.loop_forever(retry_first_connection=True)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    Bot().run()


if __name__ == '__main__':
    main()
