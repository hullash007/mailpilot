"""MailPilot Phase 1 application entrypoint."""
from fastapi import FastAPI

from .api.routes import router
from .database import Base, engine
from . import models  # noqa: F401 - register models before create_all


def create_app() -> FastAPI:
    app = FastAPI(title="MailPilot", version="0.1.0")
    Base.metadata.create_all(bind=engine)
    app.include_router(router)
    return app


app = create_app()
