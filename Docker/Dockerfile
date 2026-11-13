# Build only with Docker/release as context. No development source is copied.
FROM python:3.12-slim-bookworm AS dependencies
WORKDIR /build
COPY requirements.lock.txt ./requirements.lock.txt
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.lock.txt

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INVENTORY_ENV=production \
    INVENTORY_DEV_LOGIN=0 \
    INVENTORY_ALLOW_LOGIN_REQUESTS=0 \
    INVENTORY_REMOTE_ADMIN_ENABLED=0 \
    INVENTORY_ADMIN_IP_RESTRICTION_ENABLED=0
WORKDIR /app
COPY --from=dependencies /wheels /wheels
COPY requirements.lock.txt /tmp/requirements.lock.txt
RUN python -m pip install --no-cache-dir --no-index --find-links=/wheels -r /tmp/requirements.lock.txt \
    && rm -rf /wheels /tmp/requirements.lock.txt \
    && groupadd --gid 10001 inventory \
    && useradd --uid 10001 --gid inventory --no-create-home inventory \
    && mkdir -p /app/data && chown inventory:inventory /app/data
COPY app/ /app/
# Refuse accidental application source or incompatible bytecode at build time.
RUN python -c "import importlib.util,json,pathlib; p=pathlib.Path('/app'); m=json.loads((p/'runtime.json').read_text()); assert not list(p.rglob('*.py')); assert not list(p.rglob('*.map')); assert importlib.util.MAGIC_NUMBER.hex()==m['pyc_magic']; import container_app; print('Compiled application and web assets verified')"
USER inventory
VOLUME ["/app/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json,urllib.request; r=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3)); assert r.get('status')=='ok'"
CMD ["python", "/app/launcher.pyc"]
