# Relay — single-process demo image (the engine + HTTP adapter + static frontend mount).
#
# Build from the REPO ROOT so both `core/` (the engine) and `app/` (the frontend) are in context:
#   docker build -f api/Dockerfile -t relay .
#   docker run --rm -p 8000:8000 --env-file .env relay            # live (key from .env)
#   docker run --rm -p 8000:8000 -e RELAY_STUB=1 relay            # offline canned demo, no key
# Then open http://localhost:8000/  (the prototype) — /handle, /approve, /config, /health are same-origin.
#
# Note: the prototype loads React/ReactDOM/Babel from the unpkg CDN, so the *browser* needs internet;
# the container itself only serves static files + the API.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

# Install the engine (with provider SDKs) then the API, both editable so the package data
# (schema.sql, examples) and the app/ mount resolve from the repo layout.
COPY core/ ./core/
RUN pip install -e "./core/[providers]"

COPY api/ ./api/
RUN pip install -e ./api/

# The frontend the API mounts at /app (RELAY_APP_DIR points the resolver at it explicitly).
COPY app/ ./app/
ENV RELAY_APP_DIR=/srv/app

# A writable, durable-within-the-container run store (suspended /handle transcripts live here
# between /handle and /approve). Mount a volume at this path to persist runs across restarts.
ENV RELAY_API_STORE_DIR=/srv/run-store
RUN mkdir -p /srv/run-store/runs

# Run as a non-root user (the editable installs + run store are world-usable under /srv).
RUN useradd --create-home --uid 10001 relay \
    && chown -R relay:relay /srv
USER relay

EXPOSE 8000

# Liveness: the API answers /health (no key required) — works in both live and stub mode.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "relay_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
