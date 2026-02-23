"""
voice_agent.main
================
Application entrypoint with clean lifecycle management.

Configuration Loading Order:
    1. .env.local (local development overrides)
    2. Environment variables (container/cloud)
    3. Azure App Configuration (if configured)

Startup Steps:
    1. core     - Redis, connection manager, session state
    2. speech   - TTS/STT pools with optional warm pooling
    3. aoai     - Azure OpenAI client
    4. warmup   - Token pre-fetch, connection warmup
    5. services - Cosmos DB, ACS, phrase manager
    6. agents   - Load unified agents and scenarios
    7. mcp      - Validate MCP server connections
    8. events   - Register event handlers
"""

from __future__ import annotations

import os
import sys

# ============================================================================
# BOOTSTRAP (must run before any other imports)
# ============================================================================
# Bootstrap handles: .env loading, path setup, telemetry, App Configuration
from lifecycle.bootstrap import bootstrap_all

_bootstrap_status = bootstrap_all()

# ============================================================================
# Now safe to import application modules
# ============================================================================
import uvicorn
from api.v1.endpoints import demo_env
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry import trace

from apps.artagent.backend.api.v1.router import v1_router
from apps.artagent.backend.config import (
    ALLOWED_ORIGINS,
    DEBUG_MODE,
    DOCS_URL,
    ENABLE_AUTH_VALIDATION,
    ENABLE_DOCS,
    ENTRA_EXEMPT_PATHS,
    ENVIRONMENT,
    OPENAPI_URL,
    REDOC_URL,
    SECURE_DOCS_URL,
)
from apps.artagent.backend.src.utils.auth import validate_entraid_token
from lifecycle.dashboard import build_startup_dashboard
from lifecycle.manager import LifecycleManager
from lifecycle.steps import (
    register_agents_step,
    register_aoai_step,
    register_core_state_step,
    register_event_handlers_step,
    register_external_services_step,
    register_mcp_servers_step,
    register_speech_pools_step,
    register_transports_step,
    register_warmup_step,
)
from utils.ml_logging import get_logger

logger = get_logger("main")


# --------------------------------------------------------------------------- #
# Agent Access Helpers (exported for use by other modules)
# --------------------------------------------------------------------------- #
def get_unified_agent(app: FastAPI, name: str):
    """Get a unified agent by name from app.state."""
    return getattr(app.state, "unified_agents", {}).get(name)


def get_all_unified_agents(app: FastAPI):
    """Get all unified agents from app.state."""
    return getattr(app.state, "unified_agents", {})


def get_handoff_map(app: FastAPI):
    """Get the handoff map from app.state."""
    return getattr(app.state, "handoff_map", {})


# --------------------------------------------------------------------------- #
#  Lifecycle Management
# --------------------------------------------------------------------------- #
async def lifespan(app: FastAPI):
    """
    Manage application startup and shutdown.

    Uses the LifecycleManager for clean, modular initialization.
    Each step is defined in lifecycle/steps.py for easy maintenance.

    Deferred steps (warmup, MCP) run in the background after the app
    starts accepting requests, reducing time-to-first-request.
    """
    tracer = trace.get_tracer(__name__)
    manager = LifecycleManager()

    # Register all startup steps (order matters)
    # Note: warmup and mcp are deferred - they run after yield
    register_transports_step(manager, app)
    register_core_state_step(manager, app)
    register_speech_pools_step(manager, app)
    register_aoai_step(manager, app)
    register_warmup_step(manager, app)  # deferred=True
    register_external_services_step(manager, app)
    register_agents_step(manager, app)
    register_mcp_servers_step(manager, app)  # deferred=True
    register_event_handlers_step(manager, app)

    # Run startup (blocking steps only)
    with tracer.start_as_current_span("startup.lifespan"):
        startup_results = await manager.run_startup()

    # Log the dashboard (single info log)
    deferred_names = manager.get_deferred_step_names()
    logger.info(build_startup_dashboard(app, startup_results, deferred_names))

    # Start deferred tasks (warmup, MCP validation) in background
    manager.start_deferred_startup(app)

    # ---- Application runs ----
    yield

    # Run shutdown
    with tracer.start_as_current_span("shutdown.lifespan"):
        await manager.run_shutdown()


# --------------------------------------------------------------------------- #
#  App Factory
# --------------------------------------------------------------------------- #
def create_app() -> FastAPI:
    """Create FastAPI app with configurable documentation."""
    if ENABLE_DOCS:
        from apps.artagent.backend.api.swagger_docs import get_description, get_tags

        tags = get_tags()
        description = get_description()
    else:
        tags = None
        description = "Real-Time Voice Agent API"

    app = FastAPI(
        title="Real-Time Voice Agent API",
        description=description,
        version="1.0.0",
        contact={"name": "Real-Time Voice Agent Team", "email": "support@example.com"},
        license_info={"name": "MIT License", "url": "https://opensource.org/licenses/MIT"},
        openapi_tags=tags,
        lifespan=lifespan,
        docs_url=DOCS_URL,
        redoc_url=REDOC_URL,
        openapi_url=OPENAPI_URL,
    )

    # Add secure docs endpoint if configured
    if SECURE_DOCS_URL and ENABLE_DOCS:
        from fastapi.openapi.docs import get_swagger_ui_html

        @app.get(SECURE_DOCS_URL, include_in_schema=False)
        async def secure_docs():
            return get_swagger_ui_html(
                openapi_url=OPENAPI_URL or "/openapi.json",
                title=f"{app.title} - Secure Docs",
            )

    return app


def setup_middleware_and_routes(app: FastAPI) -> None:
    """Configure CORS, authentication, and routes."""

    # Single ASGI middleware that handles CORS (HTTP only) and request logging.
    # CORSMiddleware is used as an internal delegate for HTTP requests only.
    # WebSocket connections bypass CORS entirely — Starlette's CORSMiddleware
    # sends close code 1008 (Policy Violation) for WebSocket origins it doesn't
    # recognize, breaking server-to-server transports like Genesys AudioHook.
    class RequestLoggerMiddleware:
        """Raw ASGI middleware: CORS for HTTP + logging for HTTP & WebSocket."""

        def __init__(self, asgi_app):
            self.inner = asgi_app
            # CORS only applies to HTTP — delegate to Starlette's CORSMiddleware
            self.cors_app = CORSMiddleware(
                asgi_app,
                allow_origins=ALLOWED_ORIGINS,
                allow_credentials=True,
                allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
                allow_headers=["*"],
                max_age=86400,
            )

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http":
                path = scope.get("path", "?")
                method = scope.get("method", "?")
                client = scope.get("client")
                client_host = client[0] if client else "unknown"
                logger.info(
                    "[request-logger] %s %s from %s",
                    method,
                    path,
                    client_host,
                )

                status_code = None

                async def send_wrapper(message):
                    nonlocal status_code
                    if message["type"] == "http.response.start":
                        status_code = message.get("status", 0)
                    await send(message)

                # Route HTTP through CORSMiddleware
                await self.cors_app(scope, receive, send_wrapper)
                logger.info(
                    "[request-logger] %s %s → %s",
                    method,
                    path,
                    status_code,
                )
            elif scope["type"] == "websocket":
                path = scope.get("path", "?")
                client = scope.get("client")
                client_host = client[0] if client else "unknown"

                logger.info(
                    "[request-logger] WS %s from %s (connect)",
                    path,
                    client_host,
                )

                # Dump every route (including sub-routes) at request time
                def _collect_routes(router, prefix=""):
                    found = []
                    for r in getattr(router, "routes", []):
                        rpath = prefix + getattr(r, "path", "")
                        rtype = type(r).__name__
                        found.append(f"{rtype}:{rpath}")
                        # Recurse into Mount / APIRouter sub-routes
                        if hasattr(r, "routes"):
                            found.extend(_collect_routes(r, rpath))
                    return found

                all_routes = _collect_routes(app.router)
                logger.info(
                    "[request-logger] WS %s — all resolved routes: %s",
                    path,
                    all_routes,
                )

                # Trace ASGI messages for debugging
                async def ws_send_wrapper(message):
                    logger.info(
                        "[request-logger] WS %s SEND type=%s code=%s reason=%s",
                        path,
                        message.get("type"),
                        message.get("code", "n/a"),
                        message.get("reason", "n/a")
                    )
                    await send(message)

                async def ws_receive_wrapper():
                    message = await receive()
                    logger.info(
                        "[request-logger] WS %s RECV type=%s",
                        path,
                        message.get("type"),
                    )
                    return message

                try:
                    # Bypass CORS — go straight to inner app
                    await self.inner(scope, ws_receive_wrapper, ws_send_wrapper)
                except Exception as exc:
                    logger.error(
                        "[request-logger] WS %s EXCEPTION: %s",
                        path,
                        exc,
                        exc_info=True,
                    )
                logger.info(
                    "[request-logger] WS %s from %s (disconnect)",
                    path,
                    client_host,
                )
            else:
                await self.inner(scope, receive, send)

    app.add_middleware(RequestLoggerMiddleware)

    # Authentication middleware
    if ENABLE_AUTH_VALIDATION:

        @app.middleware("http")
        async def auth_middleware(request: Request, call_next):
            path = request.url.path
            logger.warning("Auth middleware processing request: path=%s", path)
            if any(path.startswith(p) for p in ENTRA_EXEMPT_PATHS):
                return await call_next(request)
            try:
                await validate_entraid_token(request)
            except HTTPException as e:
                logger.warning(
                    "Auth middleware rejected request: path=%s, status=%d, detail=%s",
                    path,
                    e.status_code,
                    e.detail,
                )
                return JSONResponse(content={"error": e.detail}, status_code=e.status_code)
            return await call_next(request)

    # Discover transport plugins and mount their routers on the app.
    from apps.artagent.backend.registries.transportstore import (
        discover_plugins,
        get_plugin_routers,
        register_transport,
    )
    from apps.artagent.backend.voice.transports import (
        ACSTransportAdapter,
        BrowserTransportAdapter,
    )

    register_transport("acs", ACSTransportAdapter)
    register_transport("browser", BrowserTransportAdapter)

    discovered = discover_plugins()
    if discovered:
        logger.info("Discovered external transport plugins: %s", discovered)

    for plugin_router in get_plugin_routers():
        app.include_router(plugin_router)
        prefix = getattr(plugin_router, "prefix", "")
        logger.info("Mounted transport plugin router: %s", prefix or "/")

    # Routes
    app.include_router(v1_router)
    app.include_router(demo_env.router)

    # System info endpoint
    @app.get("/api/info", tags=["System"], include_in_schema=ENABLE_DOCS)
    async def get_system_info():
        return {
            "environment": ENVIRONMENT,
            "debug_mode": DEBUG_MODE,
            "docs_enabled": ENABLE_DOCS,
            "docs_url": DOCS_URL,
            "redoc_url": REDOC_URL,
            "openapi_url": OPENAPI_URL,
            "secure_docs_url": SECURE_DOCS_URL,
        }


# --------------------------------------------------------------------------- #
#  Application Instance
# --------------------------------------------------------------------------- #
app = create_app()
setup_middleware_and_routes(app)


# --------------------------------------------------------------------------- #
#  Entry Point
# --------------------------------------------------------------------------- #
def main():
    """Entry point for uv run artagent-server."""
    port = int(os.environ.get("PORT", 8080))
    uvicorn.run(app, host="0.0.0.0", port=port, reload=False)  # nosec: B104


if __name__ == "__main__":
    main()
