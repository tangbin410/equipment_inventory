import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError
from starlette.requests import Request

from backend import database as db
from backend.auth import create_access_token, hash_password, verify_password
from backend.main import current_user
from backend.models import UserCreateRequest, UserOut, UserPasswordRequest, UserUpdateRequest


class AdminAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.encoded = hash_password('isolated-test-password-12')

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
        db.DB_DIR = Path(self.temp.name)
        db.DB_PATH = db.DB_DIR / 'accounts.db'
        db.BACKUP_DIR = db.DB_DIR / 'backups'
        db.init_db()

    def tearDown(self):
        db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
        self.temp.cleanup()

    def account(self, name='full-a', scope='full', **values):
        return db.create_user(name, name, 'admin', admin_scope=scope, password_hash=self.encoded, **values)

    def test_admin_account_created_with_password_and_scope_atomically(self):
        user = self.account('collaborator-b', 'collaborator')
        self.assertEqual(user['admin_scope'], 'collaborator')
        self.assertTrue(verify_password('isolated-test-password-12', user['password_hash']))
        self.assertNotIn('password_hash', db.get_users()[0])
        public = UserOut(**user).model_dump()
        self.assertEqual(public['admin_scope'], 'collaborator')
        self.assertNotIn('password_hash', public)
        self.assertNotIn('password', public)

    def test_existing_scope_and_password_are_preserved_by_legacy_upsert(self):
        before = self.account('collaborator-b', 'collaborator')
        after = db.create_user('collaborator-b', '更新姓名', 'admin')
        self.assertEqual(after['admin_scope'], 'collaborator')
        self.assertEqual(after['password_hash'], before['password_hash'])
        self.assertEqual(after['auth_version'], before['auth_version'])

    def test_create_only_never_overwrites_existing_or_deleted_account(self):
        existing = self.account('collaborator-b', 'collaborator')
        for deleted in (False, True):
            if deleted:
                db.delete_user(existing['id'])
            before = db.get_user_by_id(existing['id'])
            with self.subTest(deleted=deleted), self.assertRaisesRegex(ValueError, '登录编码已存在，请编辑已有账号'):
                db.create_user('collaborator-b', '不应覆盖', 'admin', admin_scope='full',
                               password_hash='untrusted-hash', update_existing=False)
            self.assertEqual(db.get_user_by_id(existing['id']), before)

    def test_last_full_admin_remains_protected_even_with_collaborator_password(self):
        full = self.account()
        self.account('collaborator-b', 'collaborator')
        mutations = [lambda: db.delete_user(full['id']),
                     lambda: db.update_user(full['id'], {'is_active': False}),
                     lambda: db.update_user(full['id'], {'role': 'worker'}),
                     lambda: db.update_user(full['id'], {'admin_scope': 'collaborator'}),
                     lambda: db.create_user('full-a', 'full-a', 'admin', admin_scope='collaborator'),
                     lambda: db.create_user('full-a', 'full-a', 'worker'),
                     lambda: db.set_password_hash(full['id'], '')]
        for mutate in mutations:
            with self.subTest(mutate=mutate), self.assertRaisesRegex(ValueError, '最后一个'):
                mutate()
        self.assertEqual(db.get_user_by_id(full['id'])['admin_scope'], 'full')

    def test_admin_without_password_or_disabled_full_does_not_replace_last_full(self):
        full = self.account()
        db.create_user('no-password', '未设密码', 'admin')
        self.account('disabled-full', is_active=0)
        with self.assertRaisesRegex(ValueError, '最后一个'):
            db.delete_user(full['id'])

    def test_scope_change_revokes_old_token_and_does_not_affect_other_accounts(self):
        self.account('full-a')
        user = self.account('collaborator-b', 'collaborator')
        with patch.dict('os.environ', {'INVENTORY_SECRET_KEY': 'isolated-account-secret-at-least-32-characters'}):
            token = create_access_token(user)
            changed = db.update_user(user['id'], {'admin_scope': 'full'})
            self.assertEqual(changed['auth_version'], user['auth_version'] + 1)
            with self.assertRaises(HTTPException) as error:
                current_user(Request({'type': 'http', 'method': 'GET', 'path': '/api/summary',
                                      'headers': [], 'client': ('127.0.0.1', 12345)}), 'Bearer ' + token)
            self.assertEqual(error.exception.status_code, 401)
        self.assertEqual(db.get_users()[0]['admin_scope'], 'full')

    def test_upsert_scope_and_password_change_increment_auth_version(self):
        self.account('full-a')
        before = self.account('collaborator-b', 'collaborator')
        after = db.create_user('collaborator-b', 'B', 'admin', admin_scope='full', password_hash='changed-hash')
        self.assertEqual(after['auth_version'], before['auth_version'] + 1)
        self.assertEqual(after['admin_scope'], 'full')
        self.assertEqual(after['password_hash'], 'changed-hash')

    def test_password_reset_increments_version_but_unchanged_hash_does_not(self):
        user = self.account()
        db.set_password_hash(user['id'], user['password_hash'])
        self.assertEqual(db.get_user_by_id(user['id'])['auth_version'], user['auth_version'])
        db.set_password_hash(user['id'], 'new-hash')
        self.assertEqual(db.get_user_by_id(user['id'])['auth_version'], user['auth_version'] + 1)

    def test_worker_promotion_sets_scope_and_password_in_one_update(self):
        self.account('full-a')
        worker = db.create_user('worker-b', '现场B', 'worker')
        promoted = db.update_user(worker['id'], {'role': 'admin', 'admin_scope': 'collaborator'}, password_hash=self.encoded)
        self.assertEqual(promoted['role'], 'admin')
        self.assertEqual(promoted['admin_scope'], 'collaborator')
        self.assertEqual(promoted['password_hash'], self.encoded)
        self.assertEqual(promoted['auth_version'], worker['auth_version'] + 1)

    def test_password_hash_cannot_be_written_through_normal_update_fields(self):
        user = self.account('collaborator-b', 'collaborator')
        updated = db.update_user(user['id'], {'name': 'B', 'password_hash': 'untrusted', 'password': 'untrusted'})
        self.assertEqual(updated['password_hash'], self.encoded)
        changed = db.update_user(user['id'], {}, password_hash='new-hash')
        self.assertEqual(changed['password_hash'], 'new-hash')
        self.assertEqual(changed['auth_version'], user['auth_version'] + 1)

    def test_restored_account_cannot_reuse_its_old_token(self):
        self.account('full-a')
        old = self.account('collaborator-b', 'collaborator')
        db.delete_user(old['id'])
        restored = db.create_user('collaborator-b', 'B', 'admin')
        self.assertEqual(restored['admin_scope'], 'collaborator')
        self.assertGreater(restored['auth_version'], old['auth_version'])

    def test_profile_change_preserves_collaborator_scope_and_session_version(self):
        user = self.account('collaborator-b', 'collaborator')
        changed = db.update_user(user['id'], {'name': 'B更正姓名', 'phone': '13800000000'})
        self.assertEqual(changed['admin_scope'], 'collaborator')
        self.assertEqual(changed['auth_version'], user['auth_version'])

    def test_invalid_or_null_scope_is_rejected(self):
        user = self.account()
        with self.assertRaisesRegex(ValueError, '权限'):
            db.create_user('invalid', 'invalid', 'admin', admin_scope='root')
        for scope in ('root', None):
            with self.subTest(scope=scope), self.assertRaises(ValueError):
                db.update_user(user['id'], {'admin_scope': scope})
        with self.assertRaises(ValidationError):
            UserUpdateRequest(admin_scope='root')

    def test_password_models_share_length_and_nonblank_requirements(self):
        for value in ('short', ' ' * 12, 'x' * 129):
            with self.subTest(value_length=len(value)):
                with self.assertRaises(ValidationError):
                    UserPasswordRequest(password=value)
                with self.assertRaises(ValidationError):
                    UserCreateRequest(wx_openid='b', name='B', role='admin', password=value)
                with self.assertRaises(ValidationError):
                    UserUpdateRequest(password=value)
        self.assertEqual(UserPasswordRequest(password='x' * 12).password, 'x' * 12)
        self.assertEqual(len(UserPasswordRequest(password='x' * 128).password), 128)
        self.assertIsNone(UserCreateRequest(wx_openid='w', name='现场').password)
        self.assertNotIn('isolated-test-password-12', repr(UserPasswordRequest(password='isolated-test-password-12')))

    def test_legacy_user_migration_defaults_to_full_and_preserves_hash(self):
        legacy_path = db.DB_DIR / 'legacy.db'
        conn = sqlite3.connect(legacy_path)
        conn.execute("CREATE TABLE users(id INTEGER PRIMARY KEY,wx_openid TEXT UNIQUE,name TEXT,role TEXT,password_hash TEXT)")
        conn.execute("INSERT INTO users VALUES(1,'old-admin','旧管理员','admin',?)", (self.encoded,))
        conn.commit()
        conn.close()
        db.DB_PATH = legacy_path
        db.init_db()
        first = db.get_user_by_id(1)
        self.assertEqual(first['admin_scope'], 'full')
        self.assertEqual(first['password_hash'], self.encoded)
        db.init_db()
        self.assertEqual(db.get_user_by_id(1)['password_hash'], self.encoded)

    def test_concurrent_disable_keeps_one_login_capable_full_admin(self):
        users = [self.account('full-a'), self.account('full-b')]
        barrier = Barrier(2)
        def disable(user):
            barrier.wait()
            try:
                db.update_user(user['id'], {'is_active': False})
                return True
            except ValueError:
                return False
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(disable, users))
        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(sum(bool(u['is_active']) for u in db.get_users() if u['role'] == 'admin' and u['admin_scope'] == 'full'), 1)


if __name__ == '__main__':
    unittest.main()
