import concurrent.futures
import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from api.auth import Identities
from api.core import Store
from api.projections import wall_projection
from api.server import Server
from edge.watcher import atomic_json, cached, Watcher


class MemoryProjection:
    def __init__(self):
        self.items = {}

    def get(self, tenant, slot):
        return self.items.get((tenant, slot))

    def put(self, tenant, slot, value, ttl=90):
        self.items[tenant, slot] = value


class SecurityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.registry = Path(cls.tmp.name)/'identities.json'
        cls.tokens = {name: name+'_'+'a'*48 for name in ('wall', 'collector', 'tech', 'approve', 'other', 'expired')}
        cls.identities = {hashlib.sha256(token.encode()).hexdigest(): dict(
            tenant='other' if name == 'other' else 'hq', actor='alice' if name in ('tech', 'approve') else name,
            role=dict(wall='observer', collector='collector', tech='technician', approve='approver', other='technician', expired='observer')[name],
            expires=time.time()+3600 if name != 'expired' else time.time()-1)
            for name, token in cls.tokens.items()}
        cls.registry.write_text(json.dumps(cls.identities))
        cls.projections = MemoryProjection()
        cls.store = Store()
        cls.server = Server(('127.0.0.1', 0), Identities(cls.registry), cls.projections, cls.store)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.store.db.close()
        cls.tmp.cleanup()

    def request(self, name=None, path='/health', method='GET', body=None, extra=None):
        connection = http.client.HTTPConnection(*self.server.server_address, timeout=3)
        headers = {'Content-Type': 'application/json', **(extra or {})}
        if name:
            headers['Authorization'] = 'Bearer '+self.tokens[name]
        connection.request(method, path, body=None if body is None else json.dumps(body), headers=headers)
        response = connection.getresponse()
        raw = response.read()
        result = (response.status, json.loads(raw) if raw else None, response.getheader('ETag'))
        connection.close()
        return result

    def test_identity_headers_cannot_authenticate(self):
        self.assertEqual(self.request(extra={'X-Tenant-ID': 'hq', 'X-Actor-ID': 'admin'})[0], 401)
        self.assertEqual(self.request('expired')[0], 401)

    def test_revocation_immediate(self):
        digest = hashlib.sha256(self.tokens['wall'].encode()).hexdigest()
        updated = {**self.identities, digest: {**self.identities[digest], 'revoked': True}}
        self.registry.write_text(json.dumps(updated))
        try:
            self.assertEqual(self.request('wall')[0], 401)
        finally:
            self.registry.write_text(json.dumps(self.identities))
        self.assertEqual(self.request('wall')[0], 200)

    def test_invalid_registry_fails_closed(self):
        for value in ('[]', '{broken'):
            self.registry.write_text(value)
            try:
                self.assertEqual(self.request('wall')[0], 401)
            finally:
                self.registry.write_text(json.dumps(self.identities))

    def test_tenant_spoof_and_approval(self):
        code, record, _ = self.request('tech', '/api/actions', 'POST', {
            'id': 'forged', 'state': 'approved', 'requested_by': 'bob', 'approved_by': 'boss'},
            extra={'X-Tenant-ID': 'other', 'X-Actor-ID': 'bob'})
        self.assertEqual(code, 201)
        self.assertNotEqual(record['id'], 'forged')
        self.assertEqual(record['requested_by'], 'alice')
        self.assertEqual(record['state'], 'proposed')
        self.assertNotIn('approved_by', record)
        self.assertEqual(self.request('other', '/api/actions/'+record['id'])[0], 404)
        self.assertEqual(self.request('approve', '/api/actions/'+record['id']+'/approve', 'POST', {})[0], 400)
        self.assertEqual(self.request('tech', '/api/actions/'+record['id']+'/approve', 'POST', {})[0], 403)
        self.assertEqual(self.request('tech', '/api/actions/'+record['id']+'/execute', 'POST', {})[0], 403)

    def test_wall_policy_and_filtered_state(self):
        for endpoint in ('tickets', 'actions', 'audit', 'policies'):
            self.assertEqual(self.request('wall', '/api/'+endpoint)[0], 403)
            self.assertEqual(self.request('wall', '/api/'+endpoint, 'POST', {})[0], 403)
        body = dict(utc=time.time(), resources=[], guests=[dict(id='qemu/1', password='secret', controllable=True)], metals=[])
        self.assertEqual(self.request('wall', '/api/wall/projection', 'POST', body)[0], 403)
        self.assertEqual(self.request('collector', '/api/wall/projection', 'POST', body)[0], 200)
        code, data, etag = self.request('wall', '/api/wall/snapshot')
        self.assertEqual(code, 200)
        self.assertNotIn('password', data['snapshot']['guests'][0])
        self.assertFalse(data['snapshot']['guests'][0]['controllable'])
        self.assertEqual(self.request('wall', '/api/wall/snapshot', extra={'If-None-Match': etag})[0], 304)
        self.assertEqual(self.request('other', '/api/wall/snapshot')[0], 503)
        self.projections.items['hq', 'wall']['utc'] = time.time()-61
        self.assertEqual(self.request('wall', '/api/wall/snapshot')[0], 503)

    def test_bounded_body_and_observer_identity(self):
        for body in ([], {'checks': [dict(name='x', status='up', latency_ms=-1)]}, {'checks': [{}]*17}):
            self.assertEqual(self.request('wall', '/api/observations', 'POST', body)[0], 400)
        self.assertEqual(self.request('wall', '/api/observations', 'POST', {'checks': [], 'actor': 'admin', 'tenant': 'other'})[0], 200)
        self.assertEqual(self.projections.items['hq', 'observer:wall']['actor'], 'wall')
        self.assertEqual(self.request('tech', '/api/tickets', 'POST', {'x': 'a'*262144})[0], 400)

    def test_concurrent_store_and_atomic_approval(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            records = list(pool.map(lambda i: self.store.create('test', 'alice', 'tickets', {'i': i}), range(40)))
        self.assertEqual(len(records), len(self.store.list('test', 'tickets')))
        action = self.store.create('test', 'alice', 'actions', {})
        def approve(actor):
            try:
                return self.store.approve('test', actor, action['id'])['approved_by']
            except ValueError:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(sum(x is not None for x in pool.map(approve, ['bob', 'carol']*4)), 1)


class CacheTests(unittest.TestCase):
    def test_offline_retains_last_good_state_and_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'cache.json'
            state = {'snapshot': {'utc': time.time()-90}, 'cursor': 'x'}
            atomic_json(path, state)
            self.assertEqual(cached(path), state)
            with self.assertRaises(ValueError):
                atomic_json(path, {'x': 'a'*262144})
            self.assertEqual(cached(path), state)
            with patch('ssl.create_default_context'):
                watcher = Watcher(dict(api='https://example.test', cache=str(path), ca_file='unused', status=str(Path(directory)/'status.json')))
            watcher.request = lambda *args, **kwargs: (_ for _ in ()).throw(OSError())
            result = watcher.cycle()
            self.assertFalse(result['api_ok'])
            self.assertGreater(result['projection_age_s'], 89)
            self.assertEqual(cached(path), state)
            watcher.request = lambda *args, **kwargs: {'snapshot': {'utc': time.time()}, 'cursor': 'new'}
            self.assertTrue(watcher.cycle(force=True)['api_ok'])
            self.assertEqual(cached(path)['cursor'], 'new')

    def test_projection_rejects_stale_and_large(self):
        with self.assertRaises(ValueError):
            wall_projection(dict(utc=0, resources=[], guests=[], metals=[]))
        with self.assertRaises(ValueError):
            wall_projection(dict(utc=time.time(), resources=[{}]*257, guests=[], metals=[]))
