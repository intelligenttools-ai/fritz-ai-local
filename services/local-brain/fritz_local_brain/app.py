"""FastAPI entry point for Local Brain."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse

from .api.routes import router
from .config import get_settings
from .scheduler import scheduler_loop
from .telemetry import sync_log_to_telemetry_quietly


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    sync_log_to_telemetry_quietly(settings)
    task: asyncio.Task | None = None
    if settings.scheduler_enabled:
        task = asyncio.create_task(scheduler_loop(settings))
    app.state.scheduler_task = task
    try:
        yield
    finally:
        app.state.scheduler_task = None
        if task:
            task.cancel()


_DASHBOARD = Path(__file__).parent / "static" / "dashboard.html"


def create_app() -> FastAPI:
    app = FastAPI(title="Fritz Local Brain", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    # Unauthenticated shell page — the page itself supplies the Bearer token
    # to the /v1/usage/* data endpoints via sessionStorage.
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard() -> FileResponse:
        return FileResponse(_DASHBOARD, media_type="text/html")

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run("fritz_local_brain.app:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
