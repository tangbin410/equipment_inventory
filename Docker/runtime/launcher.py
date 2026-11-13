"""Start the packaged web/API and an internal-only optional model agent."""

import os
import secrets
import subprocess
import sys
from pathlib import Path

import uvicorn


def main():
    root = Path(__file__).resolve().parent
    os.chdir(root)
    secret = os.getenv("INVENTORY_SECRET_KEY", "")
    password = os.getenv("INVENTORY_ADMIN_PASSWORD", "")
    if len(secret) < 32 or secret.startswith("replace-"):
        raise SystemExit("Set INVENTORY_SECRET_KEY to your own fixed random secret (32+ characters)")
    if not (root / "data" / "inventory.db").exists() and not 12 <= len(password) <= 128:
        raise SystemExit("First start requires your own INVENTORY_ADMIN_PASSWORD (12-128 characters)")
    if os.getenv("INVENTORY_REMOTE_ADMIN_ENABLED", "0") == "1" and os.getenv("INVENTORY_ADMIN_IP_RESTRICTION_ENABLED", "0") != "1":
        raise SystemExit("Cloudflare Access mode also requires INVENTORY_ADMIN_IP_RESTRICTION_ENABLED=1")
    os.environ.setdefault("INVENTORY_AGENT_TOKEN", secrets.token_urlsafe(32))
    if len(os.environ["INVENTORY_AGENT_TOKEN"]) < 24:
        raise SystemExit("INVENTORY_AGENT_TOKEN must contain at least 24 characters")
    agent = None
    if not os.getenv("INVENTORY_AI_AGENT_URL", "").strip():
        os.environ["INVENTORY_AI_AGENT_URL"] = "http://127.0.0.1:9000/analyze"
        agent = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "local_agent.app:app",
            "--host", "127.0.0.1", "--port", "9000", "--no-proxy-headers",
        ])
    try:
        uvicorn.run("container_app:app", host="0.0.0.0", port=8000, proxy_headers=False)
    finally:
        if agent is not None:
            agent.terminate()
            try:
                agent.wait(timeout=10)
            except subprocess.TimeoutExpired:
                agent.kill()
                agent.wait()


if __name__ == "__main__":
    main()
