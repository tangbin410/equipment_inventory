"""Export an already verified amd64 image and its standalone recipient package."""

import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import zipfile

import yaml


HERE = Path(__file__).resolve().parent


def checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--image", default="equipment-inventory:2026.11.12")
    args = parser.parse_args()
    docker = shutil.which(args.docker)
    if not docker:
        raise SystemExit("Docker not found; specify --docker with its executable path")
    env = os.environ.copy()
    mac_socket = Path.home() / ".docker" / "run" / "docker.sock"
    if not env.get("DOCKER_HOST") and mac_socket.exists():
        env["DOCKER_HOST"] = "unix://" + str(mac_socket)
    result = subprocess.run([docker, "image", "inspect", "--format", "{{json .}}", args.image],
                            env=env, capture_output=True, text=True, check=True)
    image = json.loads(result.stdout)
    if (image.get("Os"), image.get("Architecture")) != ("linux", "amd64"):
        raise SystemExit("This delivery requires a linux/amd64 image")
    private_keys = {"INVENTORY_SECRET_KEY", "INVENTORY_ADMIN_PASSWORD", "WECHAT_SECRET", "DEEPSEEK_API_KEY", "INVENTORY_AGENT_TOKEN"}
    for entry in image.get("Config", {}).get("Env", []):
        key, _, value = entry.partition("=")
        if key in private_keys and value:
            raise SystemExit("Refusing to export image containing private environment configuration: " + key)
    for name in ("IMAGE_README.md", "验证记录.md"):
        if not (HERE / name).is_file():
            raise SystemExit("Missing delivery document: " + name)
    output = HERE / "output" / "image-linux-amd64"
    output.mkdir(parents=True, exist_ok=True)
    private = HERE / "private"
    private.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="image-export-", dir=private) as folder:
        payload = Path(folder) / "equipment-inventory-docker-linux-amd64"
        payload.mkdir()
        image_file = payload / "equipment-inventory-image-linux-amd64.tar.gz"
        print("[1/3] Export Docker image with its base and dependency layers", flush=True)
        with tempfile.TemporaryFile() as errors, image_file.open("wb") as target:
            process = subprocess.Popen([docker, "image", "save", "--platform", "linux/amd64", args.image],
                                       env=env, stdout=subprocess.PIPE, stderr=errors)
            try:
                with gzip.GzipFile(filename="", mode="wb", compresslevel=6, fileobj=target, mtime=0) as compressed:
                    shutil.copyfileobj(process.stdout, compressed, length=1024 * 1024)
            finally:
                process.stdout.close()
            status = process.wait()
            if status:
                errors.seek(0)
                raise SystemExit("docker save failed: " + errors.read().decode(errors="replace")[:1000])
        print("[2/3] Add image-only Compose, empty configuration and recipient instructions", flush=True)
        compose = yaml.safe_load((HERE / "compose.yaml").read_text())
        service = compose["services"]["inventory"]
        service.pop("build", None)
        service.update(image=args.image, platform="linux/amd64", pull_policy="never")
        (payload / "compose.yaml").write_text(yaml.safe_dump(compose, sort_keys=False, allow_unicode=True), encoding="utf-8")
        shutil.copyfile(HERE / "runtime.env.example", payload / ".env.example")
        guide = (HERE / "IMAGE_README.md").read_text().replace("equipment-inventory:2026.11.12", args.image)
        (payload / "README.md").write_text(guide, encoding="utf-8")
        shutil.copyfile(HERE / "验证记录.md", payload / "验证记录.md")
        metadata = {"image": args.image, "id": image["Id"], "platform": "linux/amd64",
                    "image_size_bytes": image["Size"], "archive_size_bytes": image_file.stat().st_size,
                    "user": image.get("Config", {}).get("User"), "created": image.get("Created")}
        (payload / "image-info.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        lines = [f"{checksum(path)}  {path.name}" for path in sorted(payload.iterdir()) if path.is_file()]
        (payload / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")
        print("[3/3] Create three-file delivery; no development source or live data", flush=True)
        target = output / "equipment-inventory-docker-linux-amd64.zip"
        temporary = target.with_suffix(".tmp")
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
            for path in sorted(payload.iterdir()):
                archive.write(path, arcname=payload.name + "/" + path.name)
        temporary.replace(target)
        guide_file = output / "使用说明-请先阅读.md"
        guide_file.write_text(guide, encoding="utf-8")
        (output / "SHA256SUMS").write_text(f"{checksum(target)}  {target.name}\n{checksum(guide_file)}  {guide_file.name}\n", encoding="utf-8")
        print(json.dumps({**metadata, "delivery": str(target), "delivery_size_bytes": target.stat().st_size}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
