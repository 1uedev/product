"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from decision_evidence.config import get_settings
from decision_evidence.errors import install_error_handlers
from decision_evidence.identity import api as auth_api
from decision_evidence.modules.analysis import api as analysis_api
from decision_evidence.modules.customers import api as catalog_api
from decision_evidence.modules.dashboard import api as dashboard_api
from decision_evidence.modules.decisions import api as decisions_api
from decision_evidence.modules.imports import api as imports_api
from decision_evidence.modules.initiatives import api as initiatives_api
from decision_evidence.modules.members import api as members_api
from decision_evidence.modules.problems import api as problems_api
from decision_evidence.modules.scoring import api as scoring_api
from decision_evidence.observability import health
from decision_evidence.observability.logging import configure_logging, get_logger
from decision_evidence.observability.middleware import RequestContextMiddleware
from decision_evidence.public import router as public_router

log = get_logger("decision_evidence.api")


@asynccontextmanager
async def lifespan(_: FastAPI):  # type: ignore[no-untyped-def]
    settings = get_settings()
    configure_logging(settings.log_level)
    settings.validate_for_runtime(needs={"api", "storage"})
    log.info("api started", extra={"env": settings.app_env, "ai_provider": settings.ai_provider})
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="decision-evidence API", version="0.1.0", lifespan=lifespan, docs_url="/api/docs" if get_settings().app_env != "production" else None,
                  openapi_url="/api/openapi.json", redoc_url=None)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)
    for r in (health.router, public_router, auth_api.router, members_api.router, catalog_api.router, imports_api.router, analysis_api.router,
              problems_api.router, initiatives_api.router, scoring_api.router, decisions_api.router, dashboard_api.router):
        app.include_router(r)
    return app


app = create_app()
