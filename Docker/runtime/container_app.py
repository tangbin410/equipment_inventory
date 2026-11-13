"""Container-only same-origin web entry; normal developer services are unchanged."""

from pathlib import Path

from starlette.staticfiles import StaticFiles

from backend.main import app


dist = Path(__file__).resolve().parent / "admin_web" / "remote_dist"
if not (dist / "index.html").is_file():
    raise RuntimeError("Packaged admin web is missing")
# Append after existing API / QR routes, exposing only the built web directory.
app.mount("/", StaticFiles(directory=dist, html=True, follow_symlink=False), name="packaged-admin")
