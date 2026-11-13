"""Local release verification; only temporary databases and compiled app imports."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
APP = HERE / "release" / "app"
CORE_TESTS = (
    "test_admin_accounts.py",
    "test_inventory_workflow.py",
    "test_three_way_workflow.py",
    "test_inventory_pairing.py",
)


async def request(app, method, path, payload=None, token=None):
    headers = [(b"host", b"container.example.test")]
    data = b""
    if payload is not None:
        data = json.dumps(payload).encode()
        headers.append((b"content-type", b"application/json"))
    if token:
        headers.append((b"authorization", ("Bearer " + token).encode()))
    headers.append((b"content-length", str(len(data)).encode()))
    messages = []
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": data, "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1", "method": method, "scheme": "http", "path": path,
        "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": headers, "client": ("172.18.0.1", 12345),
        "server": ("container.example.test", 8000),
    }
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    start = next(message for message in messages if message["type"] == "http.response.start")
    body = b"".join(message.get("body", b"") for message in messages if message["type"] == "http.response.body")
    return start["status"], dict((key.decode(), value.decode()) for key, value in start["headers"]), body


def make_smoke_tests():
    import backend.main as main
    from backend import database as db
    import container_app
    import local_agent.prompt_loader as prompts

    class CompiledReleaseSmokeTests(unittest.IsolatedAsyncioTestCase):
        def setUp(self):
            self.temp = tempfile.TemporaryDirectory(prefix="inventory-release-test-")
            self.paths = db.DB_DIR, db.DB_PATH, db.BACKUP_DIR
            db.DB_DIR = Path(self.temp.name)
            db.DB_PATH = db.DB_DIR / "smoke.db"
            db.BACKUP_DIR = db.DB_DIR / "backups"
            main.on_startup()

        def tearDown(self):
            db.DB_DIR, db.DB_PATH, db.BACKUP_DIR = self.paths
            self.temp.cleanup()

        async def login(self):
            status, _, body = await request(container_app.app, "POST", "/api/admin/login", {
                "username": "admin_001", "password": os.environ["INVENTORY_ADMIN_PASSWORD"],
            })
            self.assertEqual(status, 200, body)
            result = json.loads(body)
            self.assertNotIn("password", result["user"])
            self.assertNotIn("password_hash", result["user"])
            return result["access_token"]

        async def test_compiled_imports_and_embedded_rules(self):
            for name, module in list(sys.modules.items()):
                if name == "container_app" or name.split(".")[0] in {"backend", "local_agent"}:
                    path = getattr(module, "__file__", None)
                    if path:
                        self.assertTrue(Path(path).resolve().is_relative_to(APP), (name, path))
                        self.assertTrue(path.endswith(".pyc"), (name, path))
            self.assertIsNone(main.__doc__)
            self.assertTrue(prompts.load_inventory_business_rules())
            self.assertFalse(list(APP.rglob("*.py")))
            self.assertFalse(list(APP.rglob("*.map")))

        async def test_homepage_and_js_are_same_origin(self):
            status, headers, body = await request(container_app.app, "GET", "/")
            self.assertEqual(status, 200)
            self.assertIn("text/html", headers["content-type"])
            scripts = re.findall(r'src="(/assets/[^" ]+\.js)"', body.decode())
            self.assertTrue(scripts, "Built page must reference a local compiled JS asset")
            for script in scripts:
                code, asset_headers, data = await request(container_app.app, "GET", script)
                self.assertEqual(code, 200)
                self.assertIn("javascript", asset_headers["content-type"])
                self.assertNotIn(b"sourceMappingURL=", data)
                self.assertNotIn(b"api.equip-inventory.xyz", data)
                self.assertNotIn(b"admin.equip-inventory.xyz", data)
                self.assertNotIn(b"http://127.0.0.1:8000/api", data)
                self.assertIn(b"/api", data)

        async def test_health_and_admin_authentication(self):
            status, _, body = await request(container_app.app, "GET", "/health")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["status"], "ok")
            status, _, _ = await request(container_app.app, "GET", "/api/summary")
            self.assertEqual(status, 401)
            status, _, _ = await request(container_app.app, "POST", "/api/admin/login", {
                "username": "admin_001", "password": "intentionally-wrong-password",
            })
            self.assertEqual(status, 401)
            token = await self.login()
            status, _, body = await request(container_app.app, "GET", "/api/admin/me", token=token)
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body)["admin_scope"], "full")
            status, _, body = await request(container_app.app, "GET", "/api/summary", token=token)
            self.assertEqual(status, 200, body)
            self.assertEqual(json.loads(body), [])

        async def test_packaged_template_download(self):
            import openpyxl

            token = await self.login()
            status, headers, body = await request(container_app.app, "GET", "/api/upload_template", token=token)
            self.assertEqual(status, 200)
            self.assertIn("spreadsheetml", headers["content-type"])
            workbook = openpyxl.load_workbook(io.BytesIO(body), read_only=True)
            self.assertEqual(tuple(next(workbook.active.values)), main.TASK_TEMPLATE_HEADERS)
            workbook.close()

        async def test_private_paths_are_not_served(self):
            for path in ("/.env", "/backend/main.pyc", "/backend/main.py", "/container_app.pyc",
                         "/data/inventory.db", "/local_agent/_packaged_prompts.pyc",
                         "/local_agent/prompts/inventory_three_way.rules.md", "/docs", "/openapi.json"):
                with self.subTest(path=path):
                    status, _, body = await request(container_app.app, "GET", path)
                    self.assertEqual(status, 404, body[:100])
            self.assertFalse((APP / "data").exists(), "Smoke tests must not create a DB inside release")

        def launcher_failure(self, changes, expected):
            env = {key: value for key, value in os.environ.items() if not key.startswith(
                ("INVENTORY_", "WECHAT_", "DEEPSEEK_", "CLOUDFLARE_", "PYTHON"))}
            env.update({
                "INVENTORY_SECRET_KEY": "isolated-launcher-verification-secret-1234567890",
                "INVENTORY_ADMIN_PASSWORD": "isolated-launcher-password-12345",
                "INVENTORY_ENV": "production", "INVENTORY_REMOTE_ADMIN_ENABLED": "0",
                "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "0",
            })
            env.update(changes)
            code = "\n".join([
                "import sys",
                "sys.path.insert(0, sys.argv[1])",
                "import launcher",
                "def refuse(*args, **kwargs):",
                "    raise RuntimeError('Test forbids starting any service')",
                "launcher.subprocess.Popen = refuse",
                "launcher.uvicorn.run = refuse",
                "launcher.main()",
            ])
            result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(APP)],
                                    cwd=self.temp.name, env=env, capture_output=True, text=True, timeout=10)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(expected, result.stderr)
            self.assertNotIn("Test forbids starting any service", result.stderr)
            self.assertFalse((APP / "data").exists(), "Rejected launcher must not create runtime data")

        async def test_launcher_rejects_missing_secret_before_starting_services(self):
            self.launcher_failure({"INVENTORY_SECRET_KEY": ""}, "Set INVENTORY_SECRET_KEY")

        async def test_launcher_rejects_access_mode_without_ip_protection(self):
            self.launcher_failure({"INVENTORY_REMOTE_ADMIN_ENABLED": "1"},
                                  "Cloudflare Access mode also requires INVENTORY_ADMIN_IP_RESTRICTION_ENABLED=1")

        async def test_launcher_uses_internal_analyze_and_chat_endpoints(self):
            env = {key: value for key, value in os.environ.items() if not key.startswith(
                ("INVENTORY_", "WECHAT_", "DEEPSEEK_", "CLOUDFLARE_", "PYTHON"))}
            env.update(INVENTORY_SECRET_KEY="isolated-launcher-secret-12345678901234567890",
                       INVENTORY_ADMIN_PASSWORD="isolated-launcher-password-12345",
                       INVENTORY_ENV="production")
            code = "\n".join([
                "import os,sys",
                "sys.path.insert(0,sys.argv[1])",
                "import launcher",
                "from unittest.mock import Mock,patch",
                "process=Mock()",
                "with patch.object(launcher.subprocess,'Popen',return_value=process) as start, patch.object(launcher.uvicorn,'run') as run:",
                "    launcher.main()",
                "    assert start.call_args.args[0][-1]=='--no-proxy-headers'",
                "    assert start.call_args.args[0][start.call_args.args[0].index('--host')+1]=='127.0.0.1'",
                "    assert run.call_args.kwargs['proxy_headers'] is False",
                "from backend.ai_agent import _external_agent_url,_external_agent_chat_url",
                "assert _external_agent_url()=='http://127.0.0.1:9000/analyze'",
                "assert _external_agent_chat_url()=='http://127.0.0.1:9000/chat'",
                "process.terminate.assert_called_once()",
                "process.wait.assert_called_once()",
            ])
            result = subprocess.run([sys.executable, "-I", "-B", "-c", code, str(APP)],
                                    cwd=self.temp.name, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((APP / "data").exists())

    return unittest.defaultTestLoader.loadTestsFromTestCase(CompiledReleaseSmokeTests)


def run_child(core_tests=True):
    if not APP.is_dir():
        raise SystemExit("Release not found; run Docker/prepare_release_mac.command first")
    sys.path.insert(0, str(APP))
    suite = unittest.TestSuite([make_smoke_tests()])
    if core_tests:
        for filename in CORE_TESTS:
            path = PROJECT / "tests" / filename
            spec = importlib.util.spec_from_file_location("release_verify_" + path.stem, path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
    with patch("requests.sessions.Session.request", side_effect=RuntimeError("Release verification does not allow network requests")):
        result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--smoke-only", action="store_true")
    args = parser.parse_args()
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Use the project CPython 3.12: .venv/bin/python Docker/test_release.py")
    if args.child:
        return run_child(core_tests=not args.smoke_only)
    env = {key: value for key, value in os.environ.items() if not key.startswith(
        ("INVENTORY_", "WECHAT_", "DEEPSEEK_", "CLOUDFLARE_", "PYTHON"))}
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "INVENTORY_SECRET_KEY": "isolated-release-verification-secret-1234567890",
        "INVENTORY_ADMIN_PASSWORD": "isolated-release-password-12345",
        "INVENTORY_ENV": "production", "INVENTORY_DEV_LOGIN": "0",
        "INVENTORY_REMOTE_ADMIN_ENABLED": "0", "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "0",
        "INVENTORY_LOGIN_RATE_LIMIT_ENABLED": "0",
    })
    with tempfile.TemporaryDirectory(prefix="inventory-release-cwd-") as temp:
        command = [sys.executable, "-I", "-B", str(Path(__file__).resolve()), "--child"]
        if args.smoke_only:
            command.append("--smoke-only")
        return subprocess.run(command, cwd=temp, env=env).returncode


if __name__ == "__main__":
    raise SystemExit(main())
