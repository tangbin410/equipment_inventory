import os
import unittest
from types import SimpleNamespace

from fastapi import HTTPException

from backend.main import (
    _LOGIN_RATE_BUCKETS,
    _check_admin_source_allowed,
    _check_dev_login_source,
    _check_login_rate_limit,
    _client_ip,
)


class FakeRequest:
    def __init__(self, headers=None, host="127.0.0.1"):
        self.headers = headers or {}
        self.client = SimpleNamespace(host=host)
        self.url = SimpleNamespace(hostname="127.0.0.1")


class LoginRateLimitTests(unittest.TestCase):
    def setUp(self):
        _LOGIN_RATE_BUCKETS.clear()
        self.original_env = {
            key: os.environ.get(key)
            for key in (
                "INVENTORY_LOGIN_RATE_LIMIT_ENABLED",
                "INVENTORY_ADMIN_LOGIN_RATE_LIMIT",
                "INVENTORY_WORKER_LOGIN_RATE_LIMIT",
                "INVENTORY_LOGIN_RATE_WINDOW_SECONDS",
                "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED",
                "INVENTORY_ADMIN_ALLOWED_IPS",
                "INVENTORY_TRUSTED_PROXY_IPS",
            )
        }

    def tearDown(self):
        _LOGIN_RATE_BUCKETS.clear()
        for key, value in self.original_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_uses_cloudflare_client_ip_when_present(self):
        request = FakeRequest({"cf-connecting-ip": "203.0.113.8"}, host="127.0.0.1")

        self.assertEqual(_client_ip(request), "203.0.113.8")

    def test_admin_login_is_limited_by_ip_and_username(self):
        os.environ["INVENTORY_ADMIN_LOGIN_RATE_LIMIT"] = "2"
        os.environ["INVENTORY_LOGIN_RATE_WINDOW_SECONDS"] = "60"
        request = FakeRequest({"x-forwarded-for": "198.51.100.7"}, host="127.0.0.1")

        _check_login_rate_limit(request, "admin", "admin_001")
        _check_login_rate_limit(request, "admin", "admin_001")
        with self.assertRaises(HTTPException) as caught:
            _check_login_rate_limit(request, "admin", "admin_001")

        self.assertEqual(caught.exception.status_code, 429)
        self.assertIn("Retry-After", caught.exception.headers)

    def test_rate_limit_can_be_disabled_for_local_troubleshooting(self):
        os.environ["INVENTORY_LOGIN_RATE_LIMIT_ENABLED"] = "0"
        os.environ["INVENTORY_WORKER_LOGIN_RATE_LIMIT"] = "1"
        request = FakeRequest(host="192.0.2.10")

        _check_login_rate_limit(request, "worker")
        _check_login_rate_limit(request, "worker")

    def test_admin_source_defaults_to_local_only(self):
        _check_admin_source_allowed(FakeRequest(host="127.0.0.1"))
        with self.assertRaises(HTTPException) as caught:
            _check_admin_source_allowed(FakeRequest({"cf-connecting-ip": "203.0.113.8"}))

        self.assertEqual(caught.exception.status_code, 404)

    def test_admin_source_accepts_explicit_cidr(self):
        os.environ["INVENTORY_ADMIN_ALLOWED_IPS"] = "203.0.113.0/24"
        _check_admin_source_allowed(FakeRequest({"cf-connecting-ip": "203.0.113.8"}))

    def test_dev_login_rejects_cloudflare_requests(self):
        _check_dev_login_source(FakeRequest(host="127.0.0.1"))
        with self.assertRaises(HTTPException) as caught:
            _check_dev_login_source(FakeRequest({"cf-connecting-ip": "203.0.113.8"}))

        self.assertEqual(caught.exception.status_code, 403)

    def test_forwarded_ip_is_ignored_from_untrusted_source(self):
        request = FakeRequest(
            {"cf-connecting-ip": "203.0.113.8"},
            host="198.51.100.10",
        )

        self.assertEqual(_client_ip(request), "198.51.100.10")


if __name__ == "__main__":
    unittest.main()
