"""MailPilot Phase 1 application entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from urllib.parse import urlsplit

from .config import get_settings
from .services.browser_boundary import BrowserBoundary

from .api.access_routes import router as access_router
from .api.routes import router
from .inbox_dashboard import router as inbox_dashboard_router
from .briefing_dashboard import router as briefing_dashboard_router
from .thread_dashboard import router as thread_dashboard_router
from .dashboard import router as dashboard_router
from .database import Base, engine, ensure_schema
from . import models  # noqa: F401 - register models before create_all


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Phase 1/2 scaffold: auto-create tables on startup, then idempotently
    # upgrade older tables (create_all never alters them). Alembic migrations
    # replace this once the schema stabilizes.
    Base.metadata.create_all(bind=engine)
    ensure_schema(engine)
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.public_origin:
        origin = urlsplit(settings.public_origin)
        if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
                or origin.path or origin.query or origin.fragment
                or not settings.cookie_secure or len(settings.app_access_token) < 32):
            raise RuntimeError("Hosted mode needs exact HTTPS PUBLIC_ORIGIN, COOKIE_SECURE=1 and an access key of at least 32 characters.")
    app = FastAPI(title="MailPilot", version="0.1.0", lifespan=lifespan,
                  docs_url=None if settings.public_origin else "/docs",
                  redoc_url=None if settings.public_origin else "/redoc",
                  openapi_url=None if settings.public_origin else "/openapi.json")
    app.add_middleware(BrowserBoundary)
    app.include_router(access_router)
    app.include_router(router)
    app.include_router(dashboard_router)
    app.include_router(thread_dashboard_router)
    app.include_router(briefing_dashboard_router)
    app.include_router(inbox_dashboard_router)
    return app


app = create_app()
