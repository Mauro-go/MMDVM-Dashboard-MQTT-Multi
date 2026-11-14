"""Multi-repeater registry and process-isolated WSGI routing."""
import atexit
import configparser
import fcntl
import html
import io
import json
import os
import re
import secrets
import signal
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from multiprocessing.connection import Connection
from flask import jsonify, request, send_from_directory, redirect
from werkzeug.wrappers import Response
from node_paths import BASE_DIR, data_file

SLUG = re.compile(r'[a-z0-9][a-z0-9-]{1,19}\Z')
NODE_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z')
RESERVED = {'api', 'admin', 'images', 'lang', 'static', 'data', 'config', 'backend', 'frontend', 'multi'}

def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.multi-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)

class Worker:
    def __init__(self, directory, slug, testing=False):
        self.lock = threading.Lock()
        self.directory, self.slug, self.testing = directory, slug, testing
        self.process = None
        self.connection = None
        self.disabled = False

    def kill_group(self):
        if self.process is not None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def retire(self):
        with self.lock:
            self.disabled = True

    def start(self):
        if self.disabled:
            return
        if self.process is not None and self.process.poll() is None:
            return
        self.kill_group()
        if self.connection:
            self.connection.close()
        parent, child = socket.socketpair()
        log = (self.directory / 'worker.log').open('ab', buffering=0)
        try:
            self.process = subprocess.Popen(
                [sys.executable, '-u', str(BASE_DIR / 'backend' / 'multi_worker.py'),
                 str(child.fileno()), str(self.directory), self.slug, 'test' if self.testing else 'run'],
                pass_fds=(child.fileno(),), stdout=log, stderr=log, start_new_session=True)
        finally:
            log.close()
            child.close()
        self.connection = Connection(parent.detach())

    def stop(self):
        with self.lock:
            if self.connection:
                try:
                    self.connection.send(None)
                except (OSError, EOFError):
                    pass
                self.connection.close()
                self.connection = None
            if self.process:
                try:
                    self.process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    self.process.terminate()
                    self.process.wait(timeout=5)
                self.kill_group()
                self.process = None

    def call(self, environ, prefix):
        with self.lock:
            if self.disabled:
                return Response('Repeater removed', status=404)
            self.start()
            length = int(environ.get('CONTENT_LENGTH') or 0)
            if length > 1024 * 1024:
                return Response('Request too large', status=413)
            body = environ['wsgi.input'].read(length) if length else b''
            clean = {key: value for key, value in environ.items() if isinstance(value, (str, bool, int))}
            clean['PATH_INFO'] = environ.get('PATH_INFO', '')[len(prefix):] or '/'
            clean['SCRIPT_NAME'] = environ.get('SCRIPT_NAME', '') + prefix
            try:
                self.connection.send((clean, body))
                if not self.connection.poll(20):
                    raise TimeoutError('Node response timeout')
                status, headers, output = self.connection.recv()
                return Response(output, status=status, headers=headers)
            except (OSError, EOFError, TimeoutError):
                if self.process and self.process.poll() is None:
                    self.kill_group()
                return Response('Repeater temporarily unavailable', status=503)

class Registry:
    def __init__(self, directory=None, testing=False):
        self.directory = Path(directory) if directory else data_file('multi')
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / 'nodes.json'
        self.lock = threading.RLock()
        self.workers = {}
        self.testing = testing
        self.stopping = threading.Event()
        self.supervisor = None
        self.nodes = self.read()
        self.finish_archives()
        atexit.register(self.close)


    def records(self):
        if not self.path.exists():
            return []
        nodes = json.loads(self.path.read_text())
        if not isinstance(nodes, list) or any(not SLUG.fullmatch(n.get('slug', '')) or n.get('archive_id') and not re.fullmatch(r'[0-9a-f]{32}', n['archive_id']) for n in nodes):
            raise ValueError('Invalid multi registry')
        return nodes

    def read(self):
        return [node for node in self.records() if not node.get('archive_id')]

    def deleted_records(self):
        return [node for node in self.records() if node.get('archive_id')]

    def save_active(self, nodes):
        atomic_json(self.path, nodes + self.deleted_records())

    def deleted(self):
        with self.lock:
            return [{key: node[key] for key in ('archive_id', 'callsign', 'node_id', 'deleted_at')} for node in self.deleted_records()]

    def finish_archives(self):
        trash = self.directory / '.trash'
        trash.mkdir(exist_ok=True, mode=0o700)
        active = {node['slug'] for node in self.nodes}
        for node in self.deleted_records():
            source = self.directory / node['slug']
            target = trash / node['archive_id']
            if not target.exists() and source.is_dir() and node['slug'] not in active:
                source.rename(target)

    def delete(self, slug):
        if not isinstance(slug, str) or not SLUG.fullmatch(slug):
            raise ValueError('Invalid repeater')
        agent_same_broker = data.get('agent_same_broker', True)
        if not isinstance(agent_same_broker, bool):
            raise ValueError('Invalid Agent same-broker setting')
        agent_host = data.get('agent_host', host)
        agent_port = data.get('agent_port', port)
        agent_user = data.get('agent_username', '')
        agent_password = data.get('agent_password', '')
        agent_tls = data.get('agent_tls', False)
        if not isinstance(agent_host, str) or not agent_host.strip() or len(agent_host) > 253 or any(ord(c) < 33 for c in agent_host):
            raise ValueError('Invalid Agent MQTT host')
        if isinstance(agent_port, bool) or not isinstance(agent_port, int) or not 1 <= agent_port <= 65535:
            raise ValueError('Invalid Agent MQTT port')
        if not isinstance(agent_user, str) or not isinstance(agent_password, str) or max(len(agent_user), len(agent_password)) > 1024 or any(ord(c) < 32 for c in agent_user + agent_password):
            raise ValueError('Invalid Agent MQTT credentials')
        if not isinstance(agent_tls, bool):
            raise ValueError('Invalid Agent MQTT TLS')
        if not agent_same_broker and agent_password and not agent_user:
            raise ValueError('Agent MQTT username required when password is set')
        with self.lock, (self.directory / '.registry.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.records()
            self.nodes = [n for n in records if not n.get('archive_id')]
            node = next((n for n in self.nodes if n['slug'] == slug), None)
            if node is None:
                raise LookupError('Repeater not found; the primary repeater cannot be removed')
            worker = self.workers.pop(slug, None)
            if worker:
                worker.retire()
                worker.stop()
            import uuid
            from datetime import datetime, timezone
            archived = dict(node, archive_id=uuid.uuid4().hex, deleted_at=datetime.now(timezone.utc).isoformat())
            source = self.directory / slug
            target = self.directory / '.trash' / archived['archive_id']
            replacement = [archived if n is node else n for n in records]
            try:
                # A committed tombstone can finish its directory move after a crash.
                atomic_json(self.path, replacement)
                source.rename(target)
            except Exception:
                atomic_json(self.path, records)
                if worker:
                    worker.disabled = False
                    self.workers[slug] = worker
                raise
            self.nodes = [n for n in replacement if not n.get('archive_id')]
            return {key: archived[key] for key in ('archive_id', 'callsign', 'node_id', 'deleted_at')}

    def restore(self, archive_id):
        if not isinstance(archive_id, str) or not re.fullmatch(r'[0-9a-f]{32}', archive_id):
            raise ValueError('Invalid archive')
        import config as dashboard_config
        from mqtt_settings import load as load_mqtt
        with self.lock, (self.directory / '.registry.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            records = self.records()
            self.nodes = [n for n in records if not n.get('archive_id')]
            archived = next((n for n in records if n.get('archive_id') == archive_id), None)
            if archived is None:
                raise LookupError('Deleted repeater not found')
            node = {key: value for key, value in archived.items() if key not in ('archive_id', 'deleted_at')}
            if node['node_id'] == dashboard_config.get('deployment', 'node_id', '').strip() or node['callsign'] == self.public()[0]['callsign'].upper() or node['topic'] == load_mqtt()['topic']:
                raise ValueError('Identity or event topic now used by the primary repeater')
            for active in self.nodes:
                if any(node[key] == active[key] for key in ('slug', 'node_id', 'topic')):
                    raise ValueError('Callsign, node_id or event topic already in use')
            prefix = node['agent_topic_prefix']
            root_prefix = dashboard_config.get('deployment', 'agent_topic_prefix', '').strip('/')
            prefixes = [n['agent_topic_prefix'] for n in self.nodes] + ([root_prefix] if root_prefix else [])
            if any(prefix == p or prefix.startswith(p + '/') or p.startswith(prefix + '/') or node['topic'] == p or node['topic'].startswith(p + '/') for p in prefixes) or any(n['topic'] == prefix or n['topic'].startswith(prefix + '/') for n in self.nodes):
                raise ValueError('Agent prefix overlaps another repeater')
            source = self.directory / '.trash' / archive_id
            target = self.directory / node['slug']
            if target.exists():
                raise ValueError('Destination directory already exists')
            if not source.is_dir():
                raise ValueError('Archived data directory is missing')
            source.rename(target)
            replacement = [node if n is archived else n for n in records]
            try:
                atomic_json(self.path, replacement)
            except Exception:
                target.rename(source)
                raise
            self.nodes = [n for n in replacement if not n.get('archive_id')]
            return node

    def public(self):
        with self.lock:
            import config as dashboard_config
            import remote_agent
            from parser import get_repeater_callsign
            detected = remote_agent.repeater_info().get('callsign', '') if remote_agent.enabled() else get_repeater_callsign()
            primary = str(detected or dashboard_config.get('repeater', 'callsign', '') or 'MMDVM').strip()
            return [{'callsign': primary, 'url': '/'}] + [
                {'callsign': n['callsign'], 'url': '/' + n['slug'] + '/'} for n in self.nodes]

    def worker(self, slug):
        with self.lock:
            if not any(n['slug'] == slug for n in self.nodes):
                raise KeyError(slug)
            if slug not in self.workers:
                self.workers[slug] = Worker(self.directory / slug, slug, self.testing)
            return self.workers[slug]

    def start_all(self):
        for node in self.nodes:
            self.worker(node['slug']).start()
        if not self.testing and self.supervisor is None:
            def supervise():
                while not self.stopping.wait(2):
                    with self.lock:
                        workers = list(self.workers.values())
                    for worker in workers:
                        with worker.lock:
                            try:
                                worker.start()
                            except OSError:
                                import logging
                                logging.exception('Multi worker restart failed: %s', worker.slug)
            self.supervisor = threading.Thread(target=supervise, name='multi-supervisor', daemon=True)
            self.supervisor.start()

    def close(self):
        self.stopping.set()
        if self.supervisor:
            self.supervisor.join(timeout=3)
        for worker in list(self.workers.values()):
            worker.stop()
        self.workers.clear()

    def add(self, data):
        import auth
        import config as dashboard_config
        from werkzeug.security import generate_password_hash
        if not isinstance(data, dict):
            raise ValueError('JSON object required')
        callsign = str(data.get('callsign', '')).strip().upper()
        slug = callsign.lower()
        node_id = str(data.get('node_id', '')).strip()
        if not SLUG.fullmatch(slug) or slug in RESERVED:
            raise ValueError('Callsign: use 2-20 letters, digits or hyphens; reserved names are unavailable')
        if not NODE_ID.fullmatch(node_id):
            raise ValueError('Invalid node_id')
        topic = str(data.get('topic', '')).strip()
        prefix = str(data.get('agent_topic_prefix', '')).strip().strip('/')
        for value in (topic, prefix):
            if not value or len(value) > 255 or any(c in value for c in '+#') or any(ord(c) < 33 for c in value):
                raise ValueError('MQTT topic and Agent prefix must be exact, without wildcards')
        if not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', prefix):
            raise ValueError('Invalid Agent prefix')
        # Agent v1 defaults to mmdvm/<node_id>/agent/v1.
        # Custom prefixes are allowed; MQTT is case-sensitive.
        # Prefix uniqueness and overlap are checked below.
        if topic == prefix or topic.startswith(prefix + '/'):
            raise ValueError('Event topic must be separate from the Agent prefix')
        host = str(data.get('host', '127.0.0.1')).strip()
        if not host or len(host) > 253 or any(ord(c) < 33 for c in host):
            raise ValueError('Invalid MQTT host')
        port = data.get('port', 1883)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Invalid MQTT port')
        username = auth._validate_username(data.get('admin_username'))
        password = data.get('admin_password')
        auth._validate_password(password)
        mqtt_user = data.get('mqtt_username', '')
        mqtt_password = data.get('mqtt_password', '')
        if not isinstance(mqtt_user, str) or not isinstance(mqtt_password, str) or max(len(mqtt_user), len(mqtt_password)) > 1024:
            raise ValueError('Invalid MQTT credentials')
        tls = data.get('tls', False)
        if not isinstance(tls, bool):
            raise ValueError('Invalid TLS setting')
        with self.lock, (self.directory / '.registry.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.nodes = self.read()
            if callsign == self.public()[0]['callsign'].upper():
                raise ValueError('Callsign already used by the primary repeater')
            root_id = dashboard_config.get('deployment', 'node_id', '').strip()
            root_prefix = dashboard_config.get('deployment', 'agent_topic_prefix', '').strip('/')
            from mqtt_settings import load as load_mqtt
            if topic == load_mqtt()['topic']:
                raise ValueError('Event topic already used by the primary repeater')
            if node_id == root_id or any(n['node_id'] == node_id for n in self.nodes):
                raise ValueError('node_id already in use')
            if any(n['slug'] == slug or n['topic'] == topic for n in self.nodes):
                raise ValueError('Callsign or event topic already in use')
            prefixes = [n['agent_topic_prefix'] for n in self.nodes] + ([root_prefix] if root_prefix else [])
            if any(prefix == p or prefix.startswith(p + '/') or p.startswith(prefix + '/') for p in prefixes):
                raise ValueError('Agent prefix overlaps another repeater')
            if any(topic == p or topic.startswith(p + '/') for p in prefixes) or any(n['topic'] == prefix or n['topic'].startswith(prefix + '/') for n in self.nodes):
                raise ValueError('Event topic overlaps an Agent prefix')
            directory = self.directory / slug
            directory.mkdir(mode=0o700)
            try:
                for name in ('data', 'admin'):
                    (directory / name).mkdir(mode=0o700)
                cfg = configparser.ConfigParser(interpolation=None)
                cfg.read(BASE_DIR / 'config' / 'dashboard.ini.example')
                if not cfg.has_section('deployment'):
                    cfg.add_section('deployment')
                cfg['deployment'].update(mode='remote', node_id=node_id, agent_topic_prefix=prefix)
                with (directory / 'dashboard.ini').open('w') as handle:
                    cfg.write(handle)
                os.chmod(directory / 'dashboard.ini', 0o600)
                atomic_json(directory / 'data' / 'mqtt-settings.json', dict(host=host, port=port, topic=topic, username=mqtt_user, password=mqtt_password, tls=tls, agent_same_broker=agent_same_broker, agent_host=agent_host, agent_port=agent_port, agent_username=agent_user, agent_password=agent_password, agent_tls=agent_tls))
                (directory / 'admin' / 'session-secret').write_text(secrets.token_hex(32))
                os.chmod(directory / 'admin' / 'session-secret', 0o600)
                atomic_json(directory / 'admin' / 'admin.json', dict(username=username, password_hash=generate_password_hash(password)))
                node = dict(slug=slug, callsign=callsign, node_id=node_id, topic=topic, agent_topic_prefix=prefix)
                nodes = self.nodes + [node]
                self.save_active(nodes)
                self.nodes = nodes
            except Exception:
                shutil.rmtree(directory)
                raise
            return node

class Router:
    def __init__(self, original, registry):
        self.original, self.registry = original, registry

    def __call__(self, environ, start_response):
        path = environ.get('PATH_INFO', '/')
        slug = path.lstrip('/').split('/', 1)[0]
        if any(n['slug'] == slug for n in self.registry.nodes):
            prefix = '/' + slug
            if path == prefix:
                response = Response('', status=308, headers={'Location': prefix + '/' + ('?' + environ['QUERY_STRING'] if environ.get('QUERY_STRING') else '')})
            else:
                try:
                    response = self.registry.worker(slug).call(environ, prefix)
                except KeyError:
                    response = Response('Repeater removed', status=404)
            return response(environ, start_response)
        return self.original(environ, start_response)

def configure_multi(app):
    slug = os.environ.get('MMDVM_MULTI_WORKER', '')
    prefix = '/' + slug if slug else ''
    if slug:
        app.config.update(SESSION_COOKIE_NAME='mmdvm_admin_' + slug, SESSION_COOKIE_PATH=prefix + '/', MAX_CONTENT_LENGTH=1024 * 1024)
        registry = None
        import config as node_config
        import mqtt_settings
        bound_deployment = {key: node_config.get('deployment', key, '') for key in ('mode', 'node_id', 'agent_topic_prefix')}
        bound_topic = mqtt_settings.load()['topic']

        @app.before_request
        def protect_node_binding():
            if request.method != 'POST':
                return None
            if request.path not in ('/api/admin/config', '/api/admin/mqtt'):
                return None
            from admin_api import _require_auth, _require_csrf
            failure = _require_auth()
            if failure is not None:
                return failure
            failure = _require_csrf()
            if failure is not None:
                return failure
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return jsonify(error='JSON object required'), 400
            if request.path.endswith('/config'):
                deployment = data.get('deployment', {})
                if not isinstance(deployment, dict) or any(key in deployment and deployment[key] != value for key, value in bound_deployment.items()):
                    return jsonify(error='Node identity is managed by Multi'), 400
            elif 'topic' in data and data['topic'] != bound_topic:
                return jsonify(error='Event topic is managed by Multi'), 400
    else:
        registry = Registry(testing=os.environ.get('MMDVM_MULTI_TESTING') == '1')
        app.extensions['multi_registry'] = registry
        app.config['MAX_CONTENT_LENGTH'] = 1024 * 1024
        app.wsgi_app = Router(app.wsgi_app, registry)

        @app.route('/api/multi/nodes')
        def multi_nodes():
            return jsonify(registry.public())

        @app.route('/admin/multi/')
        def multi_page():
            from admin_api import _authenticated
            if not _authenticated():
                return redirect('/api/admin/')
            return send_from_directory(BASE_DIR / 'admin', 'multi.html')

        @app.route('/api/admin/multi', methods=['GET', 'POST'])
        def multi_admin():
            from admin_api import _require_auth, _require_csrf, _same_origin
            failure = _require_auth()
            if failure is not None:
                return failure
            if request.method == 'GET':
                return jsonify(nodes=registry.nodes, primary=registry.public()[0], deleted=registry.deleted())
            failure = _require_csrf()
            if failure is not None:
                return failure
            if not _same_origin():
                return jsonify(error='Invalid request origin'), 403
            try:
                node = registry.add(request.get_json(silent=True))
            except (ValueError, FileExistsError) as error:
                return jsonify(error=str(error)), 400
            registry.worker(node['slug']).start()
            return jsonify(node=node), 201


        @app.before_request
        def protect_primary_binding():
            if request.method != 'POST' or request.path not in ('/api/admin/config', '/api/admin/mqtt'):
                return None
            from admin_api import _require_auth, _require_csrf
            failure = _require_auth()
            if failure is not None:
                return failure
            failure = _require_csrf()
            if failure is not None:
                return failure
            data = request.get_json(silent=True)
            if not isinstance(data, dict):
                return None
            if request.path.endswith('/mqtt'):
                topic = data.get('topic')
                if any(topic == node['topic'] for node in registry.nodes):
                    return jsonify(error='Event topic already belongs to another node'), 400
            else:
                deployment = data.get('deployment', {})
                if not isinstance(deployment, dict):
                    return None
                node_id = deployment.get('node_id')
                prefix = deployment.get('agent_topic_prefix', '')
                if not isinstance(prefix, str):
                    return jsonify(error='Invalid Agent prefix'), 400
                prefix = prefix.strip('/')
                if any(node_id == n['node_id'] or prefix and (prefix == n['agent_topic_prefix'] or prefix.startswith(n['agent_topic_prefix'] + '/') or n['agent_topic_prefix'].startswith(prefix + '/')) for n in registry.nodes):
                    return jsonify(error='Node identity or Agent prefix already belongs to another node'), 400


        @app.route('/api/admin/multi/<slug>/delete', methods=['POST'])
        @app.route('/api/admin/multi/restore/<archive_id>', methods=['POST'])
        def multi_lifecycle(slug=None, archive_id=None):
            from admin_api import _require_auth, _require_csrf, _same_origin
            failure = _require_auth()
            if failure is not None:
                return failure
            failure = _require_csrf()
            if failure is not None:
                return failure
            if not _same_origin():
                return jsonify(error='Invalid request origin'), 403
            try:
                if archive_id is not None:
                    node = registry.restore(archive_id)
                    registry.worker(node['slug']).start()
                    return jsonify(node=node)
                data = request.get_json(silent=True)
                if not isinstance(data, dict) or data.get('confirm_callsign') != slug.upper():
                    return jsonify(error='Confirm the repeater callsign'), 400
                return jsonify(deleted=registry.delete(slug))
            except LookupError as error:
                return jsonify(error=str(error)), 404
            except ValueError as error:
                return jsonify(error=str(error)), 409


        registry.start_all()

    @app.route('/admin/')
    def multi_admin_alias():
        return send_from_directory(BASE_DIR / 'admin', 'admin.html')

    @app.after_request
    def multi_navigation(response):
        if response.mimetype != 'text/html':
            return response
        response.direct_passthrough = False
        text = response.get_data(as_text=True)
        if prefix:
            # Existing frontend uses absolute URLs; scope those to this node.
            for path in ('/api/', '/images/', '/lang/', '/admin/', '/about.html', '/site.webmanifest'):
                for quote in ('"', "'", chr(96)):
                    text = text.replace(quote + path, quote + prefix + path)
            text = text.replace('href="/"', 'href="' + prefix + '/"')
        menu = '<nav id="multi-nav" style="padding:10px;background:#182431;color:white;font:14px sans-serif;display:flex;gap:14px;align-items:center"><label for="multi-select">Ripetitore</label><select id="multi-select" aria-label="Ripetitore"></select><a style="color:white" href="' + prefix + '/api/admin/">Admin</a>'
        if not slug:
            menu += '<a style="color:white" href="/admin/multi/">Gestione Multi</a>'
        if '/admin/' in request.path:
            menu += '<a style="color:white;margin-left:auto;font-weight:bold" href="' + prefix + '/">⌂ Torna alla Dashboard</a>'
        menu += '</nav><script>fetch("/api/multi/nodes",{cache:"no-store"}).then(r=>r.json()).then(nodes=>{const s=document.getElementById("multi-select");for(const n of nodes){const o=document.createElement("option");o.value=n.url;o.textContent=n.callsign;o.selected=n.url===' + json.dumps(prefix + '/') + ';s.append(o)}s.onchange=()=>location.assign(s.value)}).catch(()=>{});</script>'
        menu += '<script>window.addEventListener("multi-nodes-changed",()=>fetch("/api/multi/nodes",{cache:"no-store"}).then(r=>r.json()).then(nodes=>{const s=document.getElementById("multi-select");if(!s)return;const current=s.value;s.replaceChildren();for(const n of nodes){const o=document.createElement("option");o.value=n.url;o.textContent=n.callsign;o.selected=n.url===current;s.append(o)}}).catch(()=>{}));</script>'
        text = re.sub(r'(<body\b[^>]*>)', lambda m: m.group(1) + menu, text, count=1, flags=re.I)
        response.set_data(text)
        response.headers.pop('ETag', None)
        response.headers['Cache-Control'] = 'no-store'
        return response
