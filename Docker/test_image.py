"""Verify an existing Docker image using disposable containers, volume and credentials."""

from __future__ import annotations

import argparse
import io
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile


LABEL = "equipment-inventory.release-verification"


class VerificationError(RuntimeError):
    pass


def require(condition, description):
    if not condition:
        raise VerificationError(description)


class ImageVerification:
    def __init__(self, docker, image, platform, timeout):
        self.docker = docker
        self.image = image
        self.platform = platform
        self.timeout = timeout
        self.identifier = uuid.uuid4().hex[:16]
        self.volume = "inventory-verify-data-" + self.identifier
        self.containers = []
        self.private_values = []
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def redact(self, value):
        text = str(value)
        for secret in self.private_values:
            text = text.replace(secret, "[private]")
        return text

    def command(self, *args, check=True, timeout=60):
        try:
            result = subprocess.run([self.docker, *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise VerificationError(self.redact(f"Docker {args[0]} could not complete: {exc}")) from exc
        if check and result.returncode:
            detail = self.redact(result.stderr.strip())[:1500]
            raise VerificationError(f"Docker {args[0]} failed (exit {result.returncode}): {detail}")
        return result

    def container_info(self, name):
        result = self.command("inspect", "--format", "{{json .}}", name)
        return json.loads(result.stdout)

    def check_image(self):
        result = self.command("image", "inspect", "--format", "{{json .}}", self.image)
        info = json.loads(result.stdout)
        expected = self.platform.split("/")
        require(len(expected) >= 2, "--platform must use OS/architecture format")
        require(info.get("Os") == expected[0] and info.get("Architecture") == expected[1],
                f"Local image does not match target platform {self.platform}")
        print(f"[OK] Existing image targets {self.platform}; no image pull or build is performed", flush=True)

    def write_environment(self, directory, number, secret, password):
        fake_model_key = "verification-only-not-a-real-model-key-" + secrets.token_hex(8)
        agent_token = secrets.token_urlsafe(32)
        self.private_values.extend((secret, password, fake_model_key, agent_token))
        values = {
            "INVENTORY_SECRET_KEY": secret,
            "INVENTORY_ADMIN_PASSWORD": password,
            "INVENTORY_ENV": "production",
            "INVENTORY_DEV_LOGIN": "0",
            "INVENTORY_ALLOW_LOGIN_REQUESTS": "0",
            "INVENTORY_REMOTE_ADMIN_ENABLED": "0",
            "INVENTORY_ADMIN_IP_RESTRICTION_ENABLED": "0",
            "INVENTORY_LOGIN_RATE_LIMIT_ENABLED": "1",
            "INVENTORY_AGENT_TOKEN": agent_token,
            "INVENTORY_AI_AGENT_URL": "",
            "WECHAT_APPID": "",
            "WECHAT_SECRET": "",
            # /health only validates local configuration. An unreachable loopback
            # model URL also prevents any accidental call to a real provider.
            "DEEPSEEK_API_KEY": fake_model_key,
            "DEEPSEEK_MODEL": "isolated-image-verification",
            "DEEPSEEK_BASE_URL": "http://127.0.0.1:9",
            "DEEPSEEK_TIMEOUT_SECONDS": "2",
        }
        target = directory / f"container-{number}.env"
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
        return target

    def create_volume(self):
        self.command("volume", "create", "--label", f"{LABEL}={self.identifier}", self.volume)

    def start_container(self, number, env_file):
        name = f"inventory-verify-{self.identifier}-{number}"
        self.containers.append(name)
        self.command(
            "run", "--detach", "--pull", "never", "--platform", self.platform,
            "--name", name, "--label", f"{LABEL}={self.identifier}", "--init",
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
            "--env-file", str(env_file), "-p", "127.0.0.1::8000",
            "--mount", f"type=volume,source={self.volume},target=/app/data", self.image,
        )
        info = self.container_info(name)
        ports = info["NetworkSettings"]["Ports"].get("8000/tcp") or []
        require(len(ports) == 1 and ports[0]["HostIp"] == "127.0.0.1",
                "Test container must publish only a random localhost port")
        require(set(info["HostConfig"].get("PortBindings", {})) == {"8000/tcp"},
                "Only the web/API port may be published")
        base = "http://127.0.0.1:" + ports[0]["HostPort"]
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            info = self.container_info(name)
            require(info["State"].get("Running"), "Test container exited before its API became ready")
            try:
                status, _, body = self.http(base, "/health")
                if status == 200 and json.loads(body).get("status") == "ok":
                    print(f"[OK] Isolated container {number} started on a random localhost port", flush=True)
                    return name, base
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(1)
        raise VerificationError("Container API did not become ready within the configured timeout")

    def http(self, base, path, *, method="GET", payload=None, token=None):
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode()
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request(base + path, data=data, headers=headers, method=method)
        try:
            with self.opener.open(request, timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as response:
            return response.code, response.headers, response.read()

    def login(self, base, password, expected=200):
        status, _, body = self.http(base, "/api/admin/login", method="POST", payload={
            "username": "admin_001", "password": password,
        })
        require(status == expected, f"Administrator login returned HTTP {status}; expected {expected}")
        if expected != 200:
            return None
        result = json.loads(body)
        require("password" not in result["user"] and "password_hash" not in result["user"],
                "Login response must not expose password material")
        self.private_values.append(result["access_token"])
        return result["access_token"]

    def verify_http(self, base, password):
        status, headers, body = self.http(base, "/")
        require(status == 200 and "text/html" in headers.get("Content-Type", ""),
                "Packaged homepage was not served")
        scripts = re.findall(r'src="(/assets/[^" ]+\.js)"', body.decode())
        require(bool(scripts), "Homepage must load local compiled JavaScript")
        for script in scripts:
            status, asset_headers, code = self.http(base, script)
            require(status == 200 and "javascript" in asset_headers.get("Content-Type", ""),
                    "Packaged JavaScript asset was not served")
            require(b"/api" in code and b"sourceMappingURL=" not in code,
                    "Web assets must use same-origin API and exclude source maps")
            require(b"api.equip-inventory.xyz" not in code and b"admin.equip-inventory.xyz" not in code,
                    "Web assets must not contain the author's deployment domains")
        print("[OK] Homepage and compiled JavaScript use the same-origin web/API entry", flush=True)

        status, _, _ = self.http(base, "/api/summary")
        require(status == 401, f"Anonymous management request returned HTTP {status}; expected 401")
        self.login(base, "intentionally-incorrect-image-test-password", expected=401)
        token = self.login(base, password)
        status, _, body = self.http(base, "/api/admin/me", token=token)
        require(status == 200 and json.loads(body).get("admin_scope") == "full",
                "Authenticated administrator profile was not available")
        status, _, _ = self.http(base, "/api/summary", token=token)
        require(status == 200, "Authenticated management API was not available")
        status, _, body = self.http(base, "/api/ai/status", token=token)
        require(status == 200 and json.loads(body).get("external_agent_url") == "http://127.0.0.1:9000/analyze",
                "Backend must use the actual internal Agent /analyze endpoint")
        print("[OK] Anonymous management is rejected; private test administrator can log in", flush=True)

        status, headers, body = self.http(base, "/api/upload_template", token=token)
        require(status == 200 and "spreadsheetml" in headers.get("Content-Type", ""),
                "Authenticated inventory template download failed")
        with zipfile.ZipFile(io.BytesIO(body)) as workbook:
            require("xl/workbook.xml" in workbook.namelist(), "Downloaded template is not a valid XLSX workbook")
        print("[OK] Packaged inventory template can be downloaded", flush=True)

        for path in ("/.env", "/backend/main.py", "/backend/main.pyc", "/launcher.pyc",
                     "/container_app.pyc", "/data/inventory.db", "/local_agent/_packaged_prompts.pyc",
                     "/docs", "/openapi.json"):
            status, _, _ = self.http(base, path)
            require(status == 404, f"Private path {path} returned HTTP {status}; expected 404")
        print("[OK] Application code, environment and database files are not web-accessible", flush=True)
        return token

    def inspect_runtime(self, name):
        code = "\n".join([
            "import hashlib,json,os,pathlib,platform,sqlite3",
            "import backend.main,local_agent.prompt_loader",
            "root=pathlib.Path('/app')",
            "conn=sqlite3.connect('file:/app/data/inventory.db?mode=ro',uri=True)",
            "row=conn.execute(\"SELECT password_hash FROM users WHERE wx_openid='admin_001'\").fetchone()",
            "conn.close()",
            "print(json.dumps({'uid':os.getuid(),'machine':platform.machine(),",
            " 'py_count':len(list(root.rglob('*.py'))),'map_count':len(list(root.rglob('*.map'))),",
            " 'backend':backend.main.__file__,'prompts':bool(local_agent.prompt_loader.load_inventory_business_rules()),",
            " 'password_fingerprint':hashlib.sha256(row[0].encode()).hexdigest()}))",
        ])
        result = self.command("exec", name, "python", "-B", "-c", code)
        info = json.loads(result.stdout)
        require(info["uid"] != 0, "Container application must run as a non-root user")
        require(info["py_count"] == 0 and info["map_count"] == 0,
                "Application payload contains source code or a source map")
        require(info["backend"].endswith("/backend/main.pyc") and info["prompts"],
                "Container must import compiled backend and embedded business rules")
        if self.platform == "linux/amd64":
            require(info["machine"] == "x86_64", "Container runtime is not the requested x86_64 target")
        print("[OK] Non-root runtime contains no application .py or .map; compiled imports work", flush=True)
        return info["password_fingerprint"]

    def wait_healthy(self, name):
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            state = self.container_info(name)["State"]
            require(state.get("Running"), "Container stopped before Docker health verification")
            health = state.get("Health", {}).get("Status")
            require(health is not None, "Image must provide a Docker healthcheck")
            if health == "healthy":
                print("[OK] Docker healthcheck reports healthy", flush=True)
                break
            if health == "unhealthy":
                raise VerificationError("Docker healthcheck reports unhealthy")
            time.sleep(2)
        else:
            raise VerificationError("Docker healthcheck did not become healthy within the configured timeout")

        code = "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:9000/health',timeout=5)); assert data.get('ok') is True; print('internal-agent-health-ok')"
        result = self.command("exec", name, "python", "-B", "-c", code)
        require(result.stdout.strip() == "internal-agent-health-ok", "Internal Agent health endpoint failed")
        code = "\n".join([
            "import urllib.request,urllib.error",
            "for path in ('/analyze','/chat'):",
            "    try:",
            "        urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:9000'+path,data=b'{}',headers={'Content-Type':'application/json'}),timeout=5)",
            "        raise AssertionError('Agent must reject unauthenticated requests')",
            "    except urllib.error.HTTPError as error:",
            "        assert error.code==401, (path,error.code)",
            "print('internal-agent-routes-authenticated')",
        ])
        result = self.command("exec", name, "python", "-B", "-c", code)
        require(result.stdout.strip() == "internal-agent-routes-authenticated", "Internal Agent routes are missing or unprotected")
        print("[OK] Internal-only Agent health is available without calling a model provider", flush=True)

    def create_persistence_marker(self, base, token):
        marker = "image-verify-" + self.identifier
        status, _, _ = self.http(base, "/api/users", method="POST", token=token, payload={
            "wx_openid": marker, "name": "容器验收临时账号", "role": "worker", "area_name": "南区",
        })
        require(status == 200, "Disposable persistence marker could not be created")
        return marker

    def verify_persistence(self, base, marker, original_password, replacement_password):
        self.login(base, replacement_password, expected=401)
        token = self.login(base, original_password)
        status, _, body = self.http(base, "/api/users", token=token)
        require(status == 200 and any(user.get("wx_openid") == marker for user in json.loads(body)),
                "Volume data did not survive container replacement")
        print("[OK] Named volume retains data and the original password; new bootstrap password does not overwrite it", flush=True)

    def remove_container(self, name):
        info = self.container_info(name)
        require((info.get("Config", {}).get("Labels") or {}).get(LABEL) == self.identifier,
                "Refusing to remove a container not owned by this verification")
        self.command("rm", "--force", name)

    def cleanup(self):
        errors = []
        for name in reversed(self.containers):
            try:
                result = self.command("inspect", "--format", "{{json .Config.Labels}}", name, check=False)
                if result.returncode == 0 and (json.loads(result.stdout) or {}).get(LABEL) == self.identifier:
                    removed = self.command("rm", "--force", name, check=False)
                    if removed.returncode:
                        errors.append("Could not remove the verification container " + name)
            except (VerificationError, ValueError) as exc:
                errors.append(self.redact(exc))
        try:
            result = self.command("volume", "inspect", "--format", "{{json .Labels}}", self.volume, check=False)
            if result.returncode == 0 and (json.loads(result.stdout) or {}).get(LABEL) == self.identifier:
                removed = self.command("volume", "rm", self.volume, check=False)
                if removed.returncode:
                    errors.append("Could not remove the verification volume " + self.volume)
        except (VerificationError, ValueError) as exc:
            errors.append(self.redact(exc))
        return errors

    def run(self):
        self.check_image()
        with tempfile.TemporaryDirectory(prefix="inventory-image-private-") as folder:
            directory = Path(folder)
            secret = secrets.token_urlsafe(48)
            original_password = secrets.token_urlsafe(24)
            replacement_password = secrets.token_urlsafe(24)
            first_env = self.write_environment(directory, 1, secret, original_password)
            second_env = self.write_environment(directory, 2, secret, replacement_password)
            self.create_volume()
            first, first_base = self.start_container(1, first_env)
            token = self.verify_http(first_base, original_password)
            original_hash = self.inspect_runtime(first)
            marker = self.create_persistence_marker(first_base, token)
            self.wait_healthy(first)
            # Stop gracefully before reusing SQLite files in a replacement container.
            self.command("stop", "--time", "30", first, timeout=45)
            self.remove_container(first)
            second, second_base = self.start_container(2, second_env)
            self.verify_persistence(second_base, marker, original_password, replacement_password)
            require(self.inspect_runtime(second) == original_hash, "Stored administrator password changed during replacement")
            self.wait_healthy(second)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker", help="Docker executable, including an absolute path")
    parser.add_argument("--image", default="equipment-inventory:2026.11.12")
    parser.add_argument("--platform", default="linux/amd64")
    parser.add_argument("--timeout", type=int, default=180, help="Readiness timeout per container in seconds")
    args = parser.parse_args()
    executable = shutil.which(args.docker)
    if not executable:
        print("Docker executable not found; specify its absolute path with --docker", file=sys.stderr)
        return 2
    verification = ImageVerification(executable, args.image, args.platform, max(args.timeout, 10))
    status = 0
    try:
        verification.run()
    except (VerificationError, OSError, ValueError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        print("FAILED: " + verification.redact(exc), file=sys.stderr)
        status = 1
    except KeyboardInterrupt:
        print("Interrupted; removing only this verification's containers and volume", file=sys.stderr)
        status = 130
    finally:
        errors = verification.cleanup()
        for error in errors:
            print("Cleanup warning: " + error, file=sys.stderr)
        if errors:
            status = status or 1
    if status == 0:
        print("PASS: Docker image web/API, bytecode, health and persistence verified; disposable resources removed")
    return status


if __name__ == "__main__":
    raise SystemExit(main())
