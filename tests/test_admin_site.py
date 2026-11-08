import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote

from fastapi import HTTPException
from starlette.responses import JSONResponse

from backend.admin_site import RemoteAdminSiteMiddleware


class RemoteAdminSiteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = Path(self.temp_dir.name)
        self.dist = self.root / "remote_dist"
        self.dist.mkdir()
        (self.dist / "index.html").write_text("<html>admin shell</html>", encoding="utf-8")
        (self.dist / "assets").mkdir()
        (self.dist / "assets" / "app.js").write_text("console.log('admin')", encoding="utf-8")
        (self.dist / "assets" / "app.css").write_text("body{color:blue}", encoding="utf-8")
        self.downstream_calls = []

        async def downstream(scope, receive, send):
            self.downstream_calls.append((scope["method"], scope["path"]))
            await JSONResponse({"path": scope["path"], "method": scope["method"]})(scope, receive, send)

        self.app = RemoteAdminSiteMiddleware(downstream, dist_dir=self.dist)
        self.host = patch("backend.admin_site.admin_access.remote_admin_host", return_value="admin.example.test").start()
        self.enabled = patch("backend.admin_site.admin_access.remote_admin_enabled", return_value=True).start()
        self.verify = patch("backend.admin_site.admin_access.verify_remote_admin_request", return_value={"sub": "test-user"}).start()
        self.addCleanup(patch.stopall)

    async def request(self, path="/", *, method="GET", host="admin.example.test"):
        scope = {
            "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1", "method": method, "scheme": "https", "path": unquote(path),
            "raw_path": path.encode("utf-8"), "query_string": b"", "root_path": "",
            "headers": [(b"host", host.encode("ascii"))], "server": (host, 443), "client": ("127.0.0.1", 45000),
        }
        messages = []

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            messages.append(message)

        await self.app(scope, receive, send)
        start = next(message for message in messages if message["type"] == "http.response.start")
        headers = {key.decode(): value.decode() for key, value in start["headers"]}
        body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
        return start["status"], headers, body

    async def test_static_and_spa_routes_require_access_and_disable_cache(self):
        for path, content_type in [("/", "text/html"), ("/tasks/123", "text/html"), ("/assets/app.js", "javascript"), ("/assets/app.css", "text/css")]:
            with self.subTest(path=path):
                status, headers, body = await self.request(path)
                self.assertEqual(status, 200)
                self.assertIn(content_type, headers["content-type"])
                self.assertEqual(headers["cache-control"], "no-store")
                self.assertEqual(headers["x-content-type-options"], "nosniff")
                self.assertEqual(headers["referrer-policy"], "same-origin")
                self.assertTrue(body)
        self.assertEqual(self.verify.call_count, 4)
        self.assertFalse(self.downstream_calls)

    async def test_api_and_health_are_forwarded_without_rewriting(self):
        for path, method in [("/api", "GET"), ("/api/admin/me", "GET"), ("/api/admin/records/4/corrections", "POST"), ("/health", "GET")]:
            with self.subTest(path=path):
                status, headers, body = await self.request(path, method=method)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(body), {"path": path, "method": method})
                self.assertEqual(headers["cache-control"], "no-store")
        self.assertEqual(self.verify.call_count, 4)

    async def test_other_hosts_remain_fully_transparent(self):
        self.verify.side_effect = HTTPException(status_code=403, detail="denied")
        self.enabled.return_value = False
        for host in ["127.0.0.1:8000", "localhost:5173", "mini.example.test", "admin.example.test.evil.test"]:
            status, headers, body = await self.request("/api/scan", method="POST", host=host)
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["path"], "/api/scan")
            self.assertNotIn("cache-control", headers)
        self.verify.assert_not_called()

    async def test_invalid_access_token_cannot_reach_files_or_api(self):
        self.verify.side_effect = HTTPException(status_code=403, detail="access denied")
        for path in ["/", "/assets/app.js", "/api/login", "/health"]:
            status, headers, _ = await self.request(path)
            self.assertEqual(status, 403)
            self.assertEqual(headers["cache-control"], "no-store")
        self.assertFalse(self.downstream_calls)

    async def test_disabled_domain_is_denied_even_when_token_would_pass(self):
        self.enabled.return_value = False
        for path in ["/", "/api/admin/me"]:
            status, _, _ = await self.request(path)
            self.assertEqual(status, 403)
        self.verify.assert_not_called()
        self.assertFalse(self.downstream_calls)

    async def test_invalid_configuration_fails_closed_without_exposing_config(self):
        self.enabled.side_effect = RuntimeError("private configuration detail")
        status, headers, body = await self.request("/api/admin/me")
        self.assertEqual(status, 503)
        self.assertEqual(headers["cache-control"], "no-store")
        self.assertNotIn(b"private configuration detail", body)
        self.verify.assert_not_called()
        self.assertFalse(self.downstream_calls)

    async def test_head_serves_headers_without_body(self):
        status, headers, body = await self.request("/", method="HEAD")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["content-type"])
        self.assertEqual(body, b"")

    async def test_static_writes_are_rejected(self):
        for method in ["POST", "PUT", "DELETE", "PATCH", "OPTIONS"]:
            for path in ["/tasks", "/health"]:
                status, headers, _ = await self.request(path, method=method)
                self.assertEqual(status, 405)
                self.assertEqual(headers["allow"], "GET, HEAD")
        self.assertFalse(self.downstream_calls)

    async def test_hostname_matching_does_not_depend_on_case_or_port(self):
        status, _, body = await self.request("/", host="ADMIN.EXAMPLE.TEST:443")
        self.assertEqual(status, 200)
        self.assertIn(b"admin shell", body)
        self.verify.assert_called_once()

    async def test_unknown_static_files_and_private_routes_never_get_spa_fallback(self):
        for path in ["/assets/missing.js", "/assets/no-extension", "/missing.css", "/.env", "/.git/config", "/backend/main.py", "/src/App.vue", "/data/inventory.db", "/docs", "/docs/intro", "/openapi.json", "/redoc", "/assets/app.js.map", "/requirements.txt"]:
            with self.subTest(path=path):
                status, _, body = await self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"admin shell", body)

    async def test_existing_private_files_in_dist_are_not_served(self):
        for name in ["secret.py", "inventory.sqlite", "config.env", "README.md", "app.js.map"]:
            (self.dist / name).write_text("private content", encoding="utf-8")
            status, _, body = await self.request(f"/{name}")
            self.assertEqual(status, 404)
            self.assertNotIn(b"private content", body)

    async def test_traversal_and_symlinks_cannot_escape_dist(self):
        (self.root / "secret.js").write_text("outside secret", encoding="utf-8")
        (self.dist / "assets" / "escape.js").symlink_to(self.root / "secret.js")
        (self.dist / "outside").symlink_to(self.root, target_is_directory=True)
        (self.dist / "assets" / "loop.js").symlink_to(self.dist / "assets" / "loop.js")
        for path in ["/../secret.js", "/%2e%2e/secret.js", "/%252e%252e/secret.js", "/assets/../../secret.js", "/assets%5c..%5csecret.js", "/assets/escape.js", "/outside/secret.js", "/assets/loop.js", "/tasks/%00"]:
            with self.subTest(path=path):
                status, _, body = await self.request(path)
                self.assertEqual(status, 404)
                self.assertNotIn(b"outside secret", body)

    async def test_missing_or_external_index_is_not_served(self):
        (self.dist / "index.html").unlink()
        status, _, _ = await self.request("/tasks")
        self.assertEqual(status, 404)
        (self.root / "private.html").write_text("private shell", encoding="utf-8")
        (self.dist / "index.html").symlink_to(self.root / "private.html")
        status, _, body = await self.request("/")
        self.assertEqual(status, 404)
        self.assertNotIn(b"private shell", body)


if __name__ == "__main__":
    unittest.main()
