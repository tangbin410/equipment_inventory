import json
import os
import time
import unittest
from unittest.mock import patch

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jwt.exceptions import PyJWKClientConnectionError
from starlette.requests import Request

from backend import admin_access as access


class AdminAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.other_private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cls.private.public_key()))
        cls.jwks = {'keys': [{**public, 'kid': 'unit-key', 'alg': 'RS256', 'use': 'sig'}]}

    def setUp(self):
        self.config = {'INVENTORY_REMOTE_ADMIN_ENABLED': '1', 'INVENTORY_ADMIN_HOST': 'admin.example.com',
                       'CLOUDFLARE_ACCESS_TEAM_DOMAIN': 'https://audit-unit.cloudflareaccess.com',
                       'CLOUDFLARE_ACCESS_AUD': 'unit-audience',
                       'INVENTORY_ADMIN_ALLOWED_EMAILS': 'allowed@example.com'}
        self.environment = patch.dict(os.environ, self.config)
        self.environment.start()
        access._jwks_client.cache_clear()
        def fetch(client):
            # Mock only transport: exercise real JWKS selection, caching, RSA signature and claim checks.
            client.jwk_set_cache.put(self.jwks)
            return self.jwks
        self.fetch_patch = patch.object(jwt.PyJWKClient, 'fetch_data', autospec=True, side_effect=fetch)
        self.fetch = self.fetch_patch.start()

    def tearDown(self):
        self.fetch_patch.stop()
        self.environment.stop()
        access._jwks_client.cache_clear()

    def claims(self, **values):
        now = int(time.time())
        return dict(iss=self.config['CLOUDFLARE_ACCESS_TEAM_DOMAIN'], aud=['unit-audience'], exp=now + 300,
                    iat=now - 5, email='allowed@example.com', type='app', **values)

    def token(self, values=None, *, key=None, headers=None):
        return jwt.encode(values or self.claims(), key or self.private, algorithm='RS256',
                          headers={'kid': 'unit-key', **(headers or {})})

    def request(self, token=None, *, host='admin.example.com', extra_headers=()):
        headers = [(b'host', host.encode())]
        if token is not None:
            headers.append((b'cf-access-jwt-assertion', token.encode()))
        headers.extend(extra_headers)
        return Request({'type': 'http', 'method': 'GET', 'path': '/api/summary', 'scheme': 'https',
                        'headers': headers, 'client': ('203.0.113.10', 12345), 'server': ('audit.invalid', 443)})

    def denied(self, request, status=403):
        with self.assertRaises(HTTPException) as caught:
            access.verify_remote_admin_request(request)
        self.assertEqual(caught.exception.status_code, status)
        return caught.exception

    def test_real_rsa_token_and_verified_claims_are_cached_per_request(self):
        request = self.request(self.token())
        first = access.verify_remote_admin_request(request)
        second = access.verify_remote_admin_request(request)
        self.assertEqual(first['email'], 'allowed@example.com')
        self.assertEqual(first, second)
        self.assertEqual(request.state.remote_admin_claims, first)
        self.assertEqual(self.fetch.call_count, 1)

    def test_keyset_cache_is_shared_but_claims_are_verified_for_each_request(self):
        access.verify_remote_admin_request(self.request(self.token()))
        access.verify_remote_admin_request(self.request(self.token()))
        self.assertEqual(self.fetch.call_count, 1)
        client = access._jwks_client(self.config['CLOUDFLARE_ACCESS_TEAM_DOMAIN'])
        self.assertEqual(client.uri, 'https://audit-unit.cloudflareaccess.com/cdn-cgi/access/certs')
        self.assertEqual(client.timeout, 5)

    def test_issuer_audience_expiry_and_future_issue_time_are_checked(self):
        for change in ({'iss': 'https://foreign.cloudflareaccess.com'}, {'aud': ['foreign-app']},
                       {'exp': int(time.time()) - 10}, {'iat': int(time.time()) + 300}):
            with self.subTest(change=tuple(change)):
                self.denied(self.request(self.token({**self.claims(), **change})))

    def test_required_claims_cannot_be_omitted(self):
        for name in ('iss', 'aud', 'exp', 'iat', 'email', 'type'):
            values = self.claims()
            values.pop(name)
            with self.subTest(missing=name):
                self.denied(self.request(self.token(values)))

    def test_forged_signature_cannot_use_valid_key_identifier(self):
        self.denied(self.request(self.token(key=self.other_private)))

    def test_unsigned_hmac_and_malformed_tokens_are_rejected_before_key_fetch(self):
        tokens = [jwt.encode(self.claims(), '', algorithm='none', headers={'kid': 'unit-key'}),
                  jwt.encode(self.claims(), 'unit-test-only-hmac-key-not-a-real-secret', algorithm='HS256', headers={'kid': 'unit-key'}),
                  'not-a-jwt']
        for token in tokens:
            with self.subTest(token_type=token.split('.')[0]):
                self.denied(self.request(token))
        self.fetch.assert_not_called()

    def test_only_allowlisted_application_email_is_accepted(self):
        for change in ({'email': 'other@example.com'}, {'email': ''}, {'type': 'org'}, {'email': ['allowed@example.com']}):
            with self.subTest(change=tuple(change)):
                self.denied(self.request(self.token({**self.claims(), **change})))
        accepted = access.verify_remote_admin_request(self.request(self.token({**self.claims(), 'email': 'Allowed@Example.com'})))
        self.assertEqual(accepted['type'], 'app')

    def test_identity_email_header_is_not_a_substitute_for_signed_assertion(self):
        headers = [(b'cf-access-authenticated-user-email', b'allowed@example.com')]
        self.denied(self.request(extra_headers=headers))
        self.denied(self.request(self.token({**self.claims(), 'email': 'other@example.com'}), extra_headers=headers))

    def test_disabled_remote_admin_does_not_validate_or_fetch_keys(self):
        with patch.dict(os.environ, {'INVENTORY_REMOTE_ADMIN_ENABLED': '0', 'CLOUDFLARE_ACCESS_TEAM_DOMAIN': ''}):
            access.validate_remote_admin_config()
            self.denied(self.request(self.token()))
        self.fetch.assert_not_called()

    def test_host_must_be_exact_and_does_not_trust_forwarded_host(self):
        for host in ('api.example.com', 'admin.example.com.evil.invalid', 'admin.example.com:8000'):
            with self.subTest(host=host):
                self.denied(self.request(self.token(), host=host, extra_headers=[(b'x-forwarded-host', b'admin.example.com')]))
        self.fetch.assert_not_called()

    def test_duplicate_host_or_assertion_headers_are_rejected(self):
        token = self.token()
        self.denied(self.request(token, extra_headers=[(b'cf-access-jwt-assertion', token.encode())]))
        self.denied(self.request(token, extra_headers=[(b'host', b'admin.example.com')]))
        self.fetch.assert_not_called()

    def test_assertion_length_is_bounded_before_parsing(self):
        self.denied(self.request('x' * (access.MAX_ASSERTION_LENGTH + 1)))
        self.fetch.assert_not_called()

    def test_missing_configuration_fails_closed_without_fetching(self):
        for key in ('INVENTORY_ADMIN_HOST', 'CLOUDFLARE_ACCESS_TEAM_DOMAIN', 'CLOUDFLARE_ACCESS_AUD', 'INVENTORY_ADMIN_ALLOWED_EMAILS'):
            with self.subTest(key=key), patch.dict(os.environ, {key: ''}):
                with self.assertRaises(RuntimeError):
                    access.validate_remote_admin_config()
                self.denied(self.request(self.token()), 503)
        self.fetch.assert_not_called()

    def test_invalid_enable_flag_is_configuration_error(self):
        with patch.dict(os.environ, {'INVENTORY_REMOTE_ADMIN_ENABLED': 'true'}):
            with self.assertRaises(RuntimeError):
                access.validate_remote_admin_config()
            self.denied(self.request(self.token()), 503)

    def test_team_domain_must_be_official_https_without_ssrf_inputs(self):
        invalid = ['https://127.0.0.1', 'http://audit-unit.cloudflareaccess.com',
                   'https://audit-unit.cloudflareaccess.com.evil.invalid', 'https://audit-unit.cloudflareaccess.com@evil.invalid',
                   'https://user:password@audit-unit.cloudflareaccess.com', 'https://audit-unit.cloudflareaccess.com:8443',
                   'https://audit-unit.cloudflareaccess.com/custom', 'https://audit-unit.cloudflareaccess.com?target=localhost',
                   'https://audit-unit.cloudflareaccess.com#fragment']
        for domain in invalid:
            with self.subTest(domain=domain), patch.dict(os.environ, {'CLOUDFLARE_ACCESS_TEAM_DOMAIN': domain}):
                with self.assertRaises(RuntimeError):
                    access.validate_remote_admin_config()
                self.denied(self.request(self.token()), 503)
        self.fetch.assert_not_called()

    def test_invalid_host_or_email_configuration_is_rejected(self):
        for key, value in [('INVENTORY_ADMIN_HOST', 'https://admin.example.com'), ('INVENTORY_ADMIN_HOST', 'admin.example.com/path'),
                           ('INVENTORY_ADMIN_HOST', '127.0.0.1'), ('INVENTORY_ADMIN_ALLOWED_EMAILS', '*'),
                           ('INVENTORY_ADMIN_ALLOWED_EMAILS', 'not-an-email')]:
            with self.subTest(key=key, value=value), patch.dict(os.environ, {key: value}):
                with self.assertRaises(RuntimeError):
                    access.validate_remote_admin_config()

    def test_unknown_key_is_rejected_and_service_unavailability_returns_503(self):
        self.denied(self.request(self.token(headers={'kid': 'unknown'})))
        access._jwks_client.cache_clear()
        self.fetch.side_effect = PyJWKClientConnectionError('test-only-network-error')
        error = self.denied(self.request(self.token()), 503)
        self.assertNotIn('test-only-network-error', error.detail)

    def test_untrusted_key_urls_in_jwt_header_never_change_jwks_source(self):
        token = self.token(headers={'jku': 'http://127.0.0.1/private', 'x5u': 'https://evil.invalid/key'})
        access.verify_remote_admin_request(self.request(token))
        client = self.fetch.call_args.args[0]
        self.assertEqual(client.uri, 'https://audit-unit.cloudflareaccess.com/cdn-cgi/access/certs')

    def test_cached_claims_do_not_bypass_updated_email_policy(self):
        request = self.request(self.token())
        access.verify_remote_admin_request(request)
        with patch.dict(os.environ, {'INVENTORY_ADMIN_ALLOWED_EMAILS': 'new@example.com'}):
            self.denied(request)

    def test_timestamp_claims_must_be_integers(self):
        for change in ({'iat': False}, {'iat': str(int(time.time()) - 5)}, {'exp': str(int(time.time()) + 300)}):
            with self.subTest(change=tuple(change)):
                self.denied(self.request(self.token({**self.claims(), **change})))


if __name__ == '__main__':
    unittest.main()
