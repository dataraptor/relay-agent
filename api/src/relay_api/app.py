"""The FastAPI app: routes, the structured error envelope, CORS, and the static frontend mount.

**An adapter, not a brain (Split 07 Notes).** Zero approval/gate/loop logic lives here — every
route delegates to ``relay.handle`` / ``relay.approve`` and serializes the result via the RunView
projection. The two non-trivial concerns are cross-request persistence (the run store, ``runs.py``)
and never leaking a traceback: every §20 error path becomes a structured JSON envelope (R4).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

import relay
from relay.provider.base import MissingAPIKeyError, ProviderClient, ProviderError

from .demo_stub import build_demo_stub
from .meta import build_config, build_health, env_var_for, load_examples, stub_mode
from .runs import RunNotFoundError, RunStore, project_run_view
from .schemas import (
    ApproveRequest,
    ConfigResponse,
    ErrorEnvelope,
    ExampleTicket,
    HandleRequest,
    HealthResponse,
    RunView,
)

logger = logging.getLogger("relay_api")

# Default dev origins (same-origin needs none; these cover a separate Vite/static dev server).
_DEFAULT_CORS = ("http://localhost:5173", "http://localhost:8000", "http://127.0.0.1:8000")


class RelayHTTPError(Exception):
    """An expected, client-facing failure rendered as the structured envelope (R4)."""

    def __init__(
        self,
        status_code: int,
        type: str,
        message: str,
        *,
        provider: str | None = None,
        env_var: str | None = None,
        retriable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.type = type
        self.message = message
        self.provider = provider
        self.env_var = env_var
        self.retriable = retriable


def _envelope(
    status_code: int,
    type: str,
    message: str,
    *,
    provider: str | None = None,
    env_var: str | None = None,
    retriable: bool | None = None,
) -> JSONResponse:
    body = ErrorEnvelope.model_validate(
        {
            "error": {
                "type": type,
                "message": message,
                "provider": provider,
                "env_var": env_var,
                "retriable": retriable,
            }
        }
    )
    return JSONResponse(status_code=status_code, content=body.model_dump(exclude_none=True))


def provider_dependency() -> ProviderClient | None:
    """The provider seam for routes. Production returns ``None`` → ``relay.handle`` constructs the
    real provider from the request's ``provider``/``model``. Tests override this to inject a
    ``StubProvider`` (FastAPI dependency override) so the whole suite runs with no API key."""
    return None


def _cors_origins() -> list[str]:
    raw = os.environ.get("RELAY_CORS_ORIGINS")
    if not raw:
        return list(_DEFAULT_CORS)
    return [o.strip() for o in raw.split(",") if o.strip()]


def _app_dir() -> Path | None:
    """The static frontend directory to mount at ``/app`` (repo-root ``app/`` by default)."""
    override = os.environ.get("RELAY_APP_DIR")
    base = Path(override) if override else Path(relay.__file__).resolve().parents[3] / "app"
    return base if base.is_dir() else None


def create_app(store: RunStore | None = None) -> FastAPI:
    """Construct the app. ``store`` is injectable so tests get an isolated, temp run store."""
    app = FastAPI(
        title="Relay API",
        version="0.1.0",
        description="Thin HTTP adapter over the gated relay engine.",
    )
    app.state.store = store or RunStore()

    origins = _cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    _register_exception_handlers(app)
    _register_routes(app)
    _mount_static(app)
    return app


# ---------------------------------------------------------------------------
# Exception handlers — every failure becomes the envelope, never a 500 trace (R4)
# ---------------------------------------------------------------------------


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(RelayHTTPError)
    async def _relay_http(_: Request, exc: RelayHTTPError) -> JSONResponse:
        return _envelope(
            exc.status_code,
            exc.type,
            exc.message,
            provider=exc.provider,
            env_var=exc.env_var,
            retriable=exc.retriable,
        )

    @app.exception_handler(RunNotFoundError)
    async def _run_not_found(_: Request, exc: RunNotFoundError) -> JSONResponse:
        return _envelope(
            404, "run_not_found", f"no run found for id {str(exc)!r} (lost or expired)"
        )

    @app.exception_handler(MissingAPIKeyError)
    async def _missing_key(_: Request, exc: MissingAPIKeyError) -> JSONResponse:
        # Backstop: routes normally catch this to enrich provider/env_var (R4).
        return _envelope(424, "missing_key", str(exc), retriable=False)

    @app.exception_handler(ProviderError)
    async def _provider_error(_: Request, exc: ProviderError) -> JSONResponse:
        return _envelope(502, "provider_error", str(exc), retriable=True)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _envelope(400, "bad_request", _summarize_validation(exc))

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Last line of defence: log the trace server-side, return a neutral envelope (§20).
        logger.exception("unhandled error in relay API")
        return _envelope(500, "internal_error", "an internal error occurred")


def _summarize_validation(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = ".".join(str(p) for p in err.get("loc", ()) if p != "body")
        parts.append(f"{loc or 'body'}: {err.get('msg', 'invalid')}")
    return "invalid request: " + "; ".join(parts) if parts else "invalid request body"


# ---------------------------------------------------------------------------
# Routes (R3)
# ---------------------------------------------------------------------------


def _register_routes(app: FastAPI) -> None:
    @app.post("/handle", response_model=RunView, responses={424: {"model": ErrorEnvelope}})
    def post_handle(
        req: HandleRequest, injected: ProviderClient | None = Depends(provider_dependency)
    ) -> RunView:
        store: RunStore = app.state.store
        run_id = store.new_run_id()
        # Offline/CI demo: when no test override is injected and stub mode is on, drive the run
        # with a deterministic canned provider (R1/R4) instead of a live model. Labelled on
        # /health + /config; never silently triggered by a missing key (that stays a 424 banner).
        if injected is None and stub_mode():
            injected = build_demo_stub(req.ticket, req.provider, req.model)
        try:
            outcome = relay.handle(
                req.ticket,
                provider=req.provider,
                model=req.model,
                policy=req.policy,
                run_id=run_id,
                store_dir=store.base_dir,
                _provider=injected,
            )
        except MissingAPIKeyError as exc:
            raise RelayHTTPError(
                424,
                "missing_key",
                str(exc),
                provider=req.provider,
                env_var=env_var_for(req.provider),
                retriable=False,
            ) from exc
        except ValueError as exc:  # unknown model id / provider from make_provider
            raise RelayHTTPError(400, "bad_request", str(exc)) from exc

        store.register(run_id, injected, req.provider, req.model)
        conn = store.open_db(run_id)
        try:
            return project_run_view(outcome, conn)
        finally:
            conn.close()

    @app.post("/approve", response_model=RunView)
    def post_approve(req: ApproveRequest) -> RunView:
        store: RunStore = app.state.store
        record = store.get(req.run_id)  # RunNotFoundError -> 404 envelope
        decisions = [d.model_dump(exclude_none=True) for d in req.decisions]
        try:
            outcome = relay.approve(
                req.run_id,
                decisions,
                store_dir=store.base_dir,
                _provider=record.provider_obj,
            )
        except MissingAPIKeyError as exc:
            raise RelayHTTPError(
                424,
                "missing_key",
                str(exc),
                provider=record.provider,
                env_var=env_var_for(record.provider),
                retriable=False,
            ) from exc
        except ValueError as exc:  # missing/unknown decisions, not-awaiting, etc.
            raise RelayHTTPError(400, "bad_request", str(exc)) from exc

        conn = store.open_db(req.run_id)
        try:
            return project_run_view(outcome, conn)
        finally:
            conn.close()

    @app.get("/examples", response_model=list[ExampleTicket])
    def get_examples() -> list[ExampleTicket]:
        return load_examples()

    @app.get("/config", response_model=ConfigResponse)
    def get_config() -> ConfigResponse:
        return build_config()

    @app.get("/health", response_model=HealthResponse)
    def get_health() -> HealthResponse:
        return build_health()


# ---------------------------------------------------------------------------
# Static frontend mount (R5) — wire the point only; Split 08 makes it call the API
# ---------------------------------------------------------------------------


def _mount_static(app: FastAPI) -> None:
    app_dir = _app_dir()
    if app_dir is None:

        @app.get("/", include_in_schema=False, response_model=None)
        async def _root_no_app() -> JSONResponse:
            return JSONResponse({"service": "relay-api", "docs": "/docs"})

        return

    # Mounted at /app so the prototype's relative `./support.js` resolves and the API routes
    # (/handle, /config, …) are never shadowed. StaticFiles sets MIME types by extension.
    app.mount("/app", StaticFiles(directory=str(app_dir)), name="app")

    @app.get("/", include_in_schema=False, response_model=None)
    async def _root() -> RedirectResponse | FileResponse:
        index = app_dir / "Relay.dc.html"
        if index.is_file():
            return RedirectResponse(url="/app/Relay.dc.html")
        return FileResponse(str(index))  # pragma: no cover - dir exists ⇒ file exists


# The module-level app used by `uvicorn relay_api.app:app`.
app = create_app()
