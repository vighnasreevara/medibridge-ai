"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import get_settings
from .health import run_health
from .routes import (agents, auth, documents, encounters, events, governance, knowledge, patients, registry, staff,
                     workflow)
from .security.middleware import BoundaryMiddleware

DISCLAIMER = "Synthetic-data research/demo platform. Not approved for clinical use."


@asynccontextmanager
async def lifespan(app: FastAPI):
    worker = None
    governance.load_runtime_settings()
    if get_settings().medibridge_env != "test":
        from .services.outbox import Worker

        worker = Worker()
        worker.start()
        try:
            from medibridge_agents import runner

            runner.recover_on_startup()  # runs whose worker died with the previous process become "interrupted"
        except Exception:  # noqa: BLE001 - the API still serves records if the agent stack cannot load
            import logging

            logging.getLogger("medibridge").exception("agent runner recovery failed")
    yield
    if worker:
        worker.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="MediBridge AI",
        description=DISCLAIMER,
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(BoundaryMiddleware)
    for module in (auth, patients, registry, encounters, staff, events, documents, knowledge, workflow, agents, governance):
        app.include_router(module.router)

    @app.get("/api/health/live", tags=["system"])
    def live() -> dict:
        return {"status": "pass"}

    @app.get("/api/health", tags=["system"])
    def health() -> dict:
        return run_health()

    return app


app = create_app()
