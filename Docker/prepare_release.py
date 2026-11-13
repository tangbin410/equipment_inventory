"""Local-only release compiler. Never use the project root as Docker context."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import shutil
import subprocess
import sys
import tarfile
import tempfile

from openpyxl import load_workbook


HERE = Path(__file__).resolve().parent
PROJECT = HERE.parent
RELEASE = HERE / "release"
ARCHIVE = HERE / "output" / "equipment-inventory-runtime-py312.tar.gz"
PROMPTS = ("inventory_analysis.system.md", "inventory_analysis.user.md", "inventory_three_way.rules.md")


class RemoveDocstrings(ast.NodeTransformer):
    def strip(self, node):
        self.generic_visit(node)
        if node.body and isinstance(node.body[0], ast.Expr) and isinstance(node.body[0].value, ast.Constant) and isinstance(node.body[0].value.value, str):
            node.body.pop(0)
            if not node.body:
                node.body.append(ast.Pass())
        return node

    visit_Module = strip
    visit_ClassDef = strip
    visit_FunctionDef = strip
    visit_AsyncFunctionDef = strip


def compile_module(text: str, relative: Path, app: Path, scratch: Path):
    tree = RemoveDocstrings().visit(ast.parse(text))
    if relative.as_posix() == "local_agent/prompt_loader.py":
        tree.body.append(ast.ImportFrom(module="_packaged_prompts", names=[ast.alias(name="PROMPTS", asname="_PACKAGED_PROMPTS")], level=1))
        loader = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_read_prompt")
        loader.body = ast.parse("if name not in _PACKAGED_PROMPTS:\n    raise ValueError('invalid prompt name')\nreturn _PACKAGED_PROMPTS[name]").body
    ast.fix_missing_locations(tree)
    # Temporary source is on this machine only; it is never inside the Docker context.
    source = scratch / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(ast.unparse(tree) + "\n", encoding="utf-8")
    target = (app / relative).with_suffix(".pyc")
    target.parent.mkdir(parents=True, exist_ok=True)
    py_compile.compile(str(source), cfile=str(target), dfile="/app/" + relative.as_posix(),
                       doraise=True, optimize=0, invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH)


def check_payload(app: Path):
    permitted = {".pyc", ".json", ".xlsx", ".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".ttf", ".txt"}
    for path in app.rglob("*"):
        relative = path.relative_to(app)
        if path.is_symlink() or any(part.startswith(".") for part in relative.parts):
            raise RuntimeError(f"Private or linked file in payload: {relative}")
        if not path.is_file():
            continue
        if path.suffix not in permitted:
            raise RuntimeError(f"Unexpected runtime file: {relative}")
        if path.suffix == ".pyc" and path.read_bytes()[:4] != importlib.util.MAGIC_NUMBER:
            raise RuntimeError(f"Incompatible bytecode: {relative}")
        if path.suffix in {".js", ".css", ".html"}:
            data = path.read_text(encoding="utf-8")
            if "sourceMappingURL=" in data or "/Users/" in data or "api.equip-inventory.xyz" in data or "admin.equip-inventory.xyz" in data:
                raise RuntimeError(f"Source map / local deployment address in web artifact: {relative}")
    required = ("backend/__init__.pyc", "backend/main.pyc", "local_agent/__init__.pyc", "local_agent/_packaged_prompts.pyc", "container_app.pyc", "launcher.pyc", "templates/inventory_template.xlsx", "admin_web/remote_dist/index.html")
    for name in required:
        if not (app / name).is_file():
            raise RuntimeError(f"Missing runtime artifact: {name}")


def main():
    if sys.implementation.name != "cpython" or sys.version_info[:2] != (3, 12):
        raise SystemExit("Release bytecode requires CPython 3.12; use the project .venv/bin/python")
    if RELEASE.is_symlink() or ARCHIVE.is_symlink():
        raise SystemExit("Release/output must not be symlinks")
    if (RELEASE / "app" / "data").exists() and any((RELEASE / "app" / "data").iterdir()):
        raise SystemExit("Existing release contains runtime data; move/back up that data before rebuilding")
    if shutil.which("npm") is None:
        raise SystemExit("npm is required to build the admin web locally")
    with tempfile.TemporaryDirectory(prefix=".build-", dir=HERE) as temp:
        stage = Path(temp) / "release"
        app = stage / "app"
        app.mkdir(parents=True)
        scratch = Path(temp) / "compile-only-local"
        print("[1/4] Compile business code; keep development sources on this machine", flush=True)
        for package in ("backend", "local_agent"):
            for source in sorted((PROJECT / package).rglob("*.py")):
                if source.is_symlink():
                    raise RuntimeError(f"Refusing linked source: {source.name}")
                relative = source.relative_to(PROJECT)
                compile_module(source.read_text(encoding="utf-8"), relative, app, scratch)
        prompts = {name: (PROJECT / "local_agent" / "prompts" / name).read_text(encoding="utf-8").strip() for name in PROMPTS}
        compile_module("PROMPTS = " + repr(prompts), Path("local_agent/_packaged_prompts.py"), app, scratch)
        for name in ("container_app.py", "launcher.py"):
            compile_module((HERE / "runtime" / name).read_text(encoding="utf-8"), Path(name), app, scratch)
        (app / "templates").mkdir()
        workbook = load_workbook(PROJECT / "templates" / "inventory_template.xlsx")
        workbook.properties.creator = ""
        workbook.properties.lastModifiedBy = ""
        workbook.save(app / "templates" / "inventory_template.xlsx")
        print("[2/4] Build Vue admin web with same-origin /api; no source maps", flush=True)
        env = os.environ.copy()
        env.update(VITE_API_BASE="/api", NODE_ENV="production")
        subprocess.run(["npm", "run", "build", "--", "--outDir", str(app / "admin_web" / "remote_dist")], cwd=PROJECT / "admin_web", env=env, check=True)
        for source, target in (("Dockerfile", "Dockerfile"), (".dockerignore", ".dockerignore"), ("compose.yaml", "compose.yaml"), ("requirements.lock.txt", "requirements.lock.txt"), ("runtime.env.example", ".env.example"), ("PUBLISH_README.md", "README.md")):
            shutil.copyfile(HERE / source, stage / target)
        (app / "runtime.json").write_text(json.dumps({"python": "3.12", "pyc_magic": importlib.util.MAGIC_NUMBER.hex(), "api_base": "/api", "application_sources": False, "prompts_embedded": True}, indent=2) + "\n", encoding="utf-8")
        print("[3/4] Verify source-free payload and runtime resources", flush=True)
        check_payload(app)
        env = {k: v for k, v in os.environ.items() if not k.startswith(("INVENTORY_", "WECHAT_", "DEEPSEEK_", "CLOUDFLARE_", "PYTHONPATH"))}
        env.update(INVENTORY_ENV="production", PYTHONDONTWRITEBYTECODE="1")
        check = "import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); import container_app,backend.main,local_agent.prompt_loader as p; assert backend.main.__file__.endswith('.pyc'); assert backend.main.__doc__ is None; assert p.load_inventory_business_rules(); assert 'three_way' in p.BUSINESS_RULES_VERSION.replace('-','_'); print('Bytecode imports and embedded business rules: OK')"
        subprocess.run([sys.executable, "-I", "-c", check, str(app)], cwd=temp, env=env, check=True)
        entries = []
        for path in sorted(stage.rglob("*")):
            if path.is_file():
                entries.append(f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(stage).as_posix()}")
        (stage / "SHA256SUMS").write_text("\n".join(entries) + "\n", encoding="utf-8")
        if RELEASE.exists():
            shutil.rmtree(RELEASE)
        shutil.move(str(stage), RELEASE)
    print("[4/4] Archive only the release directory", flush=True)
    ARCHIVE.parent.mkdir(parents=True, exist_ok=True)
    temporary = ARCHIVE.with_suffix(".tmp")
    def public_metadata(info):
        info.uid = info.gid = 0
        info.uname = info.gname = ""
        info.pax_headers = {}
        return info

    with tarfile.open(temporary, "w:gz") as archive:
        archive.add(RELEASE, arcname="equipment-inventory-runtime", recursive=True, filter=public_metadata)
    temporary.replace(ARCHIVE)
    digest = hashlib.sha256(ARCHIVE.read_bytes()).hexdigest()
    recipient_guide = ARCHIVE.parent / "使用说明-请先阅读.md"
    shutil.copyfile(HERE / "RECIPIENT_README.md", recipient_guide)
    guide_digest = hashlib.sha256(recipient_guide.read_bytes()).hexdigest()
    (ARCHIVE.parent / "SHA256SUMS").write_text(f"{digest}  {ARCHIVE.name}\n{guide_digest}  {recipient_guide.name}\n", encoding="utf-8")
    print(f"Release: {RELEASE}\nUploadable archive: {ARCHIVE}\nSHA256: {digest}")
    print(f"Recipient guide: {recipient_guide}")
    print("This is a release/build-context archive, not a built Docker image.")


if __name__ == "__main__":
    main()
