import asyncio
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from backend import admin_access as access, database as db, main
from backend.auth import create_access_token, hash_password


class RemoteAdminIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """真实主程序中间件及权限依赖链；仅使用临时库、本地RSA和模拟JWKS。"""

    @classmethod
    def setUpClass(cls):
        cls.password = 'integration-only-password-12345'
        cls.password_hash = hash_password(cls.password)
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key()))
        cls.jwks = {'keys': [{**key, 'kid': 'integration-key', 'alg': 'RS256', 'use': 'sig'}]}

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / 'integration.db'
        db.BACKUP_DIR = db.DB_DIR / 'backups'
        self.config = {
            'INVENTORY_SECRET_KEY': 'isolated-integration-secret-not-for-production-12345',
            'INVENTORY_ENV': 'production', 'INVENTORY_DEV_LOGIN': '0',
            'INVENTORY_ADMIN_IP_RESTRICTION_ENABLED': '1', 'INVENTORY_ADMIN_ALLOWED_IPS': '127.0.0.1,::1',
            'INVENTORY_TRUSTED_PROXY_IPS': '127.0.0.1,::1', 'INVENTORY_LOGIN_RATE_LIMIT_ENABLED': '0',
            'INVENTORY_REMOTE_ADMIN_ENABLED': '1', 'INVENTORY_ADMIN_HOST': 'admin.example.test',
            'CLOUDFLARE_ACCESS_TEAM_DOMAIN': 'https://integration-unit.cloudflareaccess.com',
            'CLOUDFLARE_ACCESS_AUD': 'integration-audience', 'INVENTORY_ADMIN_ALLOWED_EMAILS': 'allowed@example.test',
        }
        self.environment = patch.dict(os.environ, self.config)
        self.environment.start()
        db.init_db()
        self.full = db.create_user('integration-full', '完整管理员', 'admin', '北区',
                                   admin_scope='full', password_hash=self.password_hash)
        self.collaborator = db.create_user('integration-collaborator', '协作管理员', 'admin', '北区',
                                           admin_scope='collaborator', password_hash=self.password_hash)
        self.worker = db.create_user('wx-integration-worker', '现场人员', 'worker', '南区')
        self.task = self.make_task('已完成机房')
        self.record = db.get_task_items(self.task)[0]['id']
        db.submit_scan(self.record, 'A1', False, 'C1', False, 'R1', 'M1', '', self.worker['id'])
        db.complete_inventory_task(self.task, self.worker['id'])
        self.active_task = self.make_task('正在盘点机房')
        self.active_record = db.get_task_items(self.active_task)[0]['id']
        self.dist = db.DB_DIR / 'remote_dist'
        self.dist.mkdir()
        (self.dist / 'index.html').write_text('<html>isolated admin shell</html>', encoding='utf-8')
        (self.dist / 'assets').mkdir()
        (self.dist / 'assets' / 'app.js').write_text('window.integration=true;', encoding='utf-8')
        # Build the real main.app stack without replacing its global middleware_stack.
        # Other tests may already have built that global stack using another dist path.
        with patch('backend.admin_site.DEFAULT_DIST_DIR', self.dist):
            self.app = main.app.build_middleware_stack()
        access._jwks_client.cache_clear()
        def fetch(client):
            client.jwk_set_cache.put(self.jwks)
            return self.jwks
        self.fetch_patch = patch.object(jwt.PyJWKClient, 'fetch_data', autospec=True, side_effect=fetch)
        self.fetch = self.fetch_patch.start()

    def tearDown(self):
        self.fetch_patch.stop()
        access._jwks_client.cache_clear()
        self.environment.stop()
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def make_task(self, room):
        task = db.create_task(room, room, self.worker['id'], '南区')
        with db.get_conn() as conn:
            conn.execute("UPDATE tasks SET room_code='001' WHERE id=?", (task,))
        db.batch_create_records(task, [{'device_name': '设备甲', 'expected_model': 'M1', 'expected_barcode': 'A1'}])
        return task

    def access_jwt(self, **changes):
        now = int(time.time())
        claims = dict(iss=self.config['CLOUDFLARE_ACCESS_TEAM_DOMAIN'], aud=['integration-audience'],
                      email='allowed@example.test', type='app', iat=now - 5, exp=now + 300)
        claims.update(changes)
        return jwt.encode(claims, self.private, algorithm='RS256', headers={'kid': 'integration-key'})

    def app_token(self, user=None):
        user = user or self.collaborator
        return create_access_token(user, auth_method='wechat' if user['role'] == 'worker' else 'password')

    async def request(self, method, url, payload=None, *, app_token=None, access_jwt=None,
                      host='admin.example.test', client='203.0.113.10', extra_headers=()):
        raw_path, _, query = url.partition('?')
        headers = [(b'host', host.encode()), (b'content-type', b'application/json')]
        if app_token:
            headers.append((b'authorization', ('Bearer ' + app_token).encode()))
        if access_jwt:
            headers.append((b'cf-access-jwt-assertion', access_jwt.encode()))
        headers.extend(extra_headers)
        data = json.dumps(payload or {}).encode()
        messages = []
        first = True
        async def receive():
            nonlocal first
            if first:
                first = False
                return {'type': 'http.request', 'body': data, 'more_body': False}
            await asyncio.Event().wait()
        async def send(message):
            messages.append(message)
        scope = {'type': 'http', 'asgi': {'version': '3.0', 'spec_version': '2.4'}, 'http_version': '1.1',
                 'method': method, 'scheme': 'https', 'path': unquote(raw_path), 'raw_path': raw_path.encode(),
                 'query_string': query.encode(), 'root_path': '', 'headers': headers,
                 'client': (client, 12345), 'server': (host, 443), 'app': main.app}
        await asyncio.wait_for(self.app(scope, receive, send), timeout=5)
        response = next(message for message in messages if message['type'] == 'http.response.start')
        body = b''.join(message.get('body', b'') for message in messages if message['type'] == 'http.response.body')
        response_headers = {key.decode(): value.decode() for key, value in response['headers']}
        try:
            body = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            pass
        return response['status'], body, response_headers

    async def test_remote_password_login_and_collaborator_read_permissions(self):
        gate = self.access_jwt()
        status, login, _ = await self.request('POST', '/api/admin/login', {
            'username': self.collaborator['wx_openid'], 'password': self.password,
        }, access_jwt=gate)
        self.assertEqual(status, 200)
        self.assertEqual(login['user']['admin_scope'], 'collaborator')
        self.assertNotIn('password_hash', login['user'])
        for path in ('/api/admin/me', f'/api/report/{self.task}'):
            with self.subTest(path=path):
                status, result, headers = await self.request('GET', path, app_token=login['access_token'], access_jwt=gate)
                self.assertEqual(status, 200)
                self.assertEqual(headers['cache-control'], 'no-store')
                self.assertTrue(result)
        for method, path in [('GET', '/api/users'), ('DELETE', f'/api/tasks/{self.task}')]:
            with self.subTest(path=path):
                self.assertEqual((await self.request(method, path, app_token=login['access_token'], access_jwt=gate))[0], 403)
        self.assertIsNotNone(db.get_task_by_id(self.task))

    async def test_access_jwt_alone_cannot_authenticate_backend_api(self):
        for path in ('/api/admin/me', f'/api/report/{self.task}'):
            self.assertEqual((await self.request('GET', path, access_jwt=self.access_jwt()))[0], 401)

    async def test_backend_token_without_access_is_blocked_across_entire_admin_domain(self):
        for path in ('/', '/assets/app.js', '/health', '/api/admin/me', f'/api/report/{self.task}',
                     '/api/users', '/api', '/api%2fusers', '/api%252fusers', '//api/users'):
            with self.subTest(path=path):
                self.assertEqual((await self.request('GET', path, app_token=self.app_token()))[0], 403)

    async def test_access_grant_does_not_bypass_admin_origin_policy_on_api_domain(self):
        for path in ('/api/admin/me', f'/api/report/{self.task}'):
            with self.subTest(path=path):
                self.assertEqual((await self.request('GET', path, host='api.example.test',
                                                     app_token=self.app_token(), access_jwt=self.access_jwt()))[0], 404)

    async def test_valid_signature_for_unapproved_email_cannot_reach_site_or_api(self):
        for path in ('/', '/api/admin/login', '/api/admin/me'):
            with self.subTest(path=path):
                self.assertEqual((await self.request('GET', path, app_token=self.app_token(),
                                                     access_jwt=self.access_jwt(email='outsider@example.test')))[0], 403)

    async def test_wechat_worker_on_regular_api_domain_requires_no_access_jwt(self):
        token = self.app_token(self.worker)
        for path in ('/api/tasks/my', f'/api/tasks/{self.active_task}/items', f'/api/report/{self.task}'):
            self.assertEqual((await self.request('GET', path, host='api.example.test', app_token=token))[0], 200)
        status, result, _ = await self.request('POST', '/api/scan', {
            'record_id': self.active_record, 'asset_barcodes': 'A1', 'cabinet_barcodes': 'C1',
            'rack_no': 'R1', 'actual_model': '现场手填型号',
        }, host='api.example.test', app_token=token)
        self.assertEqual(status, 200)
        self.assertTrue(result['success'])
        self.assertEqual(db.get_record_by_id(self.active_record)['actual_model'], '现场手填型号')
        self.fetch.assert_not_called()

    async def test_local_full_admin_stays_available_without_access_jwt(self):
        status, login, _ = await self.request('POST', '/api/admin/login', {
            'username': self.full['wx_openid'], 'password': self.password,
        }, host='localhost', client='127.0.0.1')
        self.assertEqual(status, 200)
        self.assertEqual((await self.request('GET', '/api/users', host='localhost', client='127.0.0.1',
                                             app_token=login['access_token']))[0], 200)
        self.fetch.assert_not_called()

    async def test_incomplete_remote_configuration_blocks_only_admin_host(self):
        with patch.dict(os.environ, {'CLOUDFLARE_ACCESS_AUD': ''}):
            for path in ('/', '/api/admin/me'):
                self.assertEqual((await self.request('GET', path, app_token=self.app_token(), access_jwt=self.access_jwt()))[0], 503)
            self.assertEqual((await self.request('GET', '/api/tasks/my', host='api.example.test',
                                                 app_token=self.app_token(self.worker)))[0], 200)
            self.assertEqual((await self.request('GET', '/api/users', host='localhost', client='127.0.0.1',
                                                 app_token=self.app_token(self.full)))[0], 200)

    async def test_static_site_is_same_origin_and_uses_isolated_build(self):
        for path, expected in [('/', b'isolated admin shell'), ('/assets/app.js', b'window.integration=true')]:
            status, body, headers = await self.request('GET', path, access_jwt=self.access_jwt())
            self.assertEqual(status, 200)
            self.assertIn(expected, body)
            self.assertEqual(headers['cache-control'], 'no-store')
        for path in ('/.env', '/data/inventory.db', '/backend/main.py', '/openapi.json', '/assets/app.js.map'):
            self.assertEqual((await self.request('GET', path, access_jwt=self.access_jwt()))[0], 404)

    async def test_encoded_api_paths_cannot_expand_collaborator_permissions(self):
        token, gate = self.app_token(), self.access_jwt()
        for method, path in [('GET', '/api/%75sers'), ('DELETE', f'/api/tasks/%{ord(str(self.task)[0]):02x}{str(self.task)[1:]}')]:
            with self.subTest(path=path):
                self.assertEqual((await self.request(method, path, app_token=token, access_jwt=gate))[0], 403)
        for path in ('/api/users-extra', '/api//users', '/api/users/../users'):
            status, _, _ = await self.request('GET', path, app_token=token, access_jwt=gate)
            self.assertEqual(status, 404)
        self.assertIsNotNone(db.get_task_by_id(self.task))

    async def test_signed_access_does_not_allow_incorrect_backend_password(self):
        status, result, _ = await self.request('POST', '/api/admin/login', {
            'username': self.collaborator['wx_openid'], 'password': 'incorrect-test-password',
        }, access_jwt=self.access_jwt())
        self.assertEqual(status, 401)
        self.assertNotIn('access_token', result)


if __name__ == '__main__':
    unittest.main()
