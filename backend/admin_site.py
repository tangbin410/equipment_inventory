"""Serve the isolated remote admin build after Cloudflare Access verification."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from urllib.parse import unquote

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import admin_access


DEFAULT_DIST_DIR = Path(__file__).resolve().parent.parent / "admin_web" / "remote_dist"
_PUBLIC_FILE_SUFFIXES = {
    ".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".avif", ".ico", ".woff", ".woff2", ".ttf", ".otf", ".eot",
}
_PRIVATE_ROUTES = {"backend", "src", "data", "docs", "redoc", "openapi.json", "local_agent", "tests", "node_modules", "admin_web"}


class RemoteAdminSiteMiddleware:
    def __init__(self, app: ASGIApp, dist_dir: str | Path | None = None) -> None:
        self.app = app
        self.dist_dir = Path(dist_dir) if dist_dir is not None else DEFAULT_DIST_DIR

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope, receive=receive)
        configured_host = admin_access.remote_admin_host()
        if not configured_host or request.url.hostname != configured_host:
            await self.app(scope, receive, send)
            return

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Referrer-Policy"] = "same-origin"
                headers["X-Frame-Options"] = "DENY"
                headers["Content-Security-Policy"] = "frame-ancestors 'none'"
            await send(message)

        try:
            if not admin_access.remote_admin_enabled():
                raise HTTPException(status_code=403, detail="远程管理入口未启用")
            await run_in_threadpool(admin_access.verify_remote_admin_request, request)
        except HTTPException as exc:
            await JSONResponse({"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers)(scope, receive, secured_send)
            return
        except RuntimeError:
            await JSONResponse({"detail": "公网后台访问配置不完整，请联系管理员"}, status_code=503)(scope, receive, secured_send)
            return

        path = scope["path"]
        if path == "/api" or path.startswith("/api/"):
            await self.app(scope, receive, secured_send)
            return
        if request.method not in {"GET", "HEAD"}:
            await JSONResponse({"detail": "Method Not Allowed"}, status_code=405, headers={"Allow": "GET, HEAD"})(scope, receive, secured_send)
            return
        if path == "/health":
            await self.app(scope, receive, secured_send)
            return

        file_path = self._public_file(path)
        if file_path is None:
            await JSONResponse({"detail": "Not Found"}, status_code=404)(scope, receive, secured_send)
            return
        await FileResponse(file_path)(scope, receive, secured_send)

    def _public_file(self, path: str) -> Path | None:
        # ASGI paths are already decoded once; decode again before checking encoded traversal.
        for _ in range(3):
            decoded = unquote(path)
            if decoded == path:
                break
            path = decoded
        if "\\" in path or "\x00" in path:
            return None
        parts = path.lstrip("/").split("/")
        if any(part.startswith(".") for part in parts) or parts[0].lower() in _PRIVATE_ROUTES:
            return None

        try:
            root = self.dist_dir.resolve()
            relative = path.lstrip("/")
            candidate = (root / relative).resolve()
            if not candidate.is_relative_to(root):
                return None
            if candidate.is_file():
                return candidate if candidate.suffix.lower() in _PUBLIC_FILE_SUFFIXES else None
            # Missing static files must stay 404 rather than returning HTML to a script request.
            if relative and (candidate.is_dir() or PurePosixPath(relative).suffix or parts[0].lower() == "assets"):
                return None
            index = (root / "index.html").resolve()
            return index if index.is_relative_to(root) and index.is_file() else None
        except (OSError, RuntimeError, ValueError):
            return None
