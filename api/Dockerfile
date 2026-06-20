# Relay API — single-process demo image (the engine + HTTP adapter + static frontend mount).
#
# Build from the REPO ROOT so both `core/` (the engine) and `app/` (the frontend) are in context:
#   docker build -f api/Dockerfile -t relay-api .
#   docker run --rm -p 8000:8000 --env-file .env relay-api
# Then open http://localhost:8000/  (the prototype) — /handle, /config, /health are same-origin.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
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

EXPOSE 8000
CMD ["uvicorn", "relay_api.app:app", "--host", "0.0.0.0", "--port", "8000"]
