"""MailPilot Phase 1 application entrypoint."""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from .api.access_routes import router as access_router
from .api.routes import router
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
    app = FastAPI(title="MailPilot", version="0.1.0", lifespan=lifespan)
    app.include_router(access_router)
    app.include_router(router)
    app.include_router(dashboard_router)
    app.include_router(thread_dashboard_router)
    return app


app = create_app()
