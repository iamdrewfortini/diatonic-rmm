"""Authenticated observer API with bounded projections; no endpoint execution route."""
import json
import os
from pathlib import Path
import ssl
import sqlite3
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
from api.auth import AuthError, Identities
from api.core import Store
from api.projections import Valkey, cursor, encode, observation, wall_projection

RESOURCES = {'tickets', 'endpoints', 'events', 'documents', 'actions', 'policies'}


class Handler(BaseHTTPRequestHandler):
    server_version = 'DiatonicObserver'
    sys_version = ''

    def log_message(self, fmt, *args):
        pass

    def send_json(self, status, value=None, etag=None):
        body = b'' if status == 304 else encode(value)
        self.send_response(status)
        for key, val in {'Content-Type': 'application/json', 'Content-Length': str(len(body)),
                         'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
                         'Connection': 'close'}.items():
            self.send_header(key, val)
        if etag:
            self.send_header('ETag', etag)
        if status == 401:
            self.send_header('WWW-Authenticate', 'Bearer')
        self.end_headers()
        self.wfile.write(body)

    def principal(self):
        if len(self.headers.get_all('Authorization', [])) != 1:
            raise AuthError()
        return self.server.identities.authenticate(self.headers.get('Authorization'))

    def dispatch(self, post=False):
        path = urlparse(self.path).path.strip('/').split('/')
        try:
            principal = self.principal()
            tenant, actor, role = (principal[k] for k in ('tenant', 'actor', 'role'))
            if not post and path == ['health']:
                return self.send_json(200, {'status': 'ok', 'mode': 'observer-demo'})
            if not post and path == ['api', 'wall', 'snapshot']:
                if role not in {'observer', 'technician', 'approver'}:
                    return self.send_json(403, {'error': 'role denied'})
                state = self.server.projections.get(tenant, 'wall')
                if not state or time.time()-state['utc'] > 60:
                    return self.send_json(503, {'error': 'projection unavailable or stale'})
                etag = cursor(state)
                if self.headers.get('If-None-Match') == etag:
                    return self.send_json(304, etag=etag)
                return self.send_json(200, {'snapshot': state, 'cursor': etag}, etag=etag)
            if post and path == ['api', 'wall', 'projection']:
                if role != 'collector':
                    return self.send_json(403, {'error': 'role denied'})
                state = wall_projection(self.read_body())
                self.server.projections.put(tenant, 'wall', state)
                return self.send_json(200, {'accepted': True})
            if post and path == ['api', 'observations']:
                if role != 'observer':
                    return self.send_json(403, {'error': 'role denied'})
                data = observation(self.read_body())
                data.update(actor=actor, device=principal.get('device', actor))
                self.server.projections.put(tenant, 'observer:'+actor, data, ttl=180)
                return self.send_json(200, {'accepted': True})
            if role in {'observer', 'collector'}:
                return self.send_json(403, {'error': 'role denied'})
            store = self.server.store
            if not post:
                if path == ['api', 'audit']:
                    return self.send_json(200, store.audit(tenant))
                if len(path) == 2 and path[0] == 'api' and path[1] in RESOURCES:
                    return self.send_json(200, store.list(tenant, path[1]))
                if len(path) == 3 and path[0] == 'api' and path[1] in RESOURCES:
                    item = store.get(tenant, path[1], path[2])
                    return self.send_json(200 if item else 404, item or {'error': 'not found'})
            elif role == 'technician' and len(path) == 2 and path[0] == 'api' and path[1] in RESOURCES-{'policies'}:
                return self.send_json(201, store.create(tenant, actor, path[1], self.read_body()))
            elif role == 'approver' and len(path) == 4 and path[:2] == ['api', 'actions'] and path[3] == 'approve':
                return self.send_json(200, store.approve(tenant, actor, path[2]))
            elif post:
                return self.send_json(403, {'error': 'role denied; execution is disabled'})
            return self.send_json(404, {'error': 'not found'})
        except AuthError:
            return self.send_json(401, {'error': 'valid unexpired identity required'})
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return self.send_json(400, {'error': 'invalid request'})
        except LookupError:
            return self.send_json(404, {'error': 'not found'})
        except (OSError, RuntimeError, sqlite3.Error):
            return self.send_json(503, {'error': 'service dependency unavailable'})

    def read_body(self):
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get('Transfer-Encoding') or len(lengths) != 1 or not lengths[0].isdigit():
            raise ValueError('invalid framing')
        length = int(lengths[0])
        if not 2 <= length <= 262144:
            raise ValueError('invalid body size')
        if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
            raise ValueError('JSON required')
        data = self.rfile.read(length)
        if len(data) != length:
            raise ValueError('truncated body')
        body = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(body, dict):
            raise ValueError('object required')
        return body

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch(post=True)


class Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, address, identities, projections, store, tls=None):
        self.identities, self.projections, self.store, self.tls = identities, projections, store, tls
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            request.settimeout(5)
            if self.tls:
                request = self.tls.wrap_socket(request, server_side=True)
            super().process_request_thread(request, address)
        except (OSError, ssl.SSLError):
            self.shutdown_request(request)
        finally:
            self.slots.release()


def main():
    config = json.loads(Path(os.environ.get('RMM_CONFIG', '/etc/diatonic-rmm/server.json')).read_text())
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(config['certificate'], config['private_key'])
    valkey = Valkey(Path(config['valkey_password_file']).read_text().strip())
    Server((config['bind'], config['port']), Identities(config['identities']), valkey,
           Store(config['database']), tls).serve_forever()


if __name__ == '__main__':
    main()
