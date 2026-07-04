"""FastAPI entry point for Local Brain."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import mcp_server
from .api.routes import router
from .config import get_settings
from .scheduler import scheduler_loop
from .telemetry import prune_old_events_quietly, sync_log_to_telemetry_quietly


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    sync_log_to_telemetry_quietly(settings)
    prune_old_events_quietly(settings)
    # #208: always start the scheduler loop — it idles when scheduler_enabled is
    # False and resumes on the next cycle when a live PATCH re-enables it, so a
    # pause/resume no longer needs a service restart. The stop event lets the
    # lifespan cancel it cleanly on shutdown.
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler_loop(settings, stop=stop))
    app.state.scheduler_task = task
    app.state.scheduler_stop = stop
    # #236: the MCP streamable-HTTP transport mounted at /mcp needs its session
    # manager running. A mounted sub-app's lifespan does NOT run under FastAPI, so
    # start it here (once) alongside the scheduler.
    async with mcp_server.mcp.session_manager.run():
        try:
            yield
        finally:
            app.state.scheduler_task = None
            stop.set()
            task.cancel()


_UI_DIR = Path(__file__).parent / "static" / "ui"

# Clean-path pages (#220): each maps to a real HTML file under _UI_DIR. These
# give deep-linkable URLs without a .html suffix (e.g. /ui/activity). The
# StaticFiles mount below also serves them as /ui/activity.html and the shared
# assets under /ui/shared/.
_UI_PAGES = {
    "activity": "activity.html",
    "agents": "agents.html",
    "operations": "operations.html",
    "settings": "settings.html",
    "knowledge": "knowledge.html",
}


def create_app() -> FastAPI:
    app = FastAPI(title="Fritz Local Brain", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    # #220: the old single-page /dashboard now lives under the /ui/ app shell.
    @app.get("/dashboard", include_in_schema=False)
    async def dashboard() -> RedirectResponse:
        return RedirectResponse(url="/ui/", status_code=307)

    # Clean deep-linkable page paths without a .html suffix (e.g. /ui/activity).
    # Registered as EXACT paths (not a catch-all) so the .html form and the
    # /ui/shared/ assets fall through to the StaticFiles mount below. Uses a
    # default-arg closure so each route binds its own filename.
    def _make_page_route(filename: str):
        async def _serve() -> FileResponse:
            return FileResponse(_UI_DIR / filename, media_type="text/html")

        return _serve

    for _clean, _filename in _UI_PAGES.items():
        app.add_api_route(
            f"/ui/{_clean}", _make_page_route(_filename),
            methods=["GET"], include_in_schema=False,
        )

    # Unauthenticated shell + shared assets. html=True makes /ui/ serve
    # index.html and /ui/activity.html resolve directly. Each page supplies the
    # Bearer token to the /v1/* data endpoints via sessionStorage.
    app.mount("/ui", StaticFiles(directory=str(_UI_DIR), html=True), name="ui")

    # #236: expose the existing MCP toolset over streamable HTTP on the same 8765
    # port (no Dockerfile/compose change). The mount also lazily creates the
    # session manager that `lifespan` starts. Bearer-auth is enforced by the
    # wrapper in mcp_server.streamable_http_app.
    app.mount("/mcp", mcp_server.streamable_http_app())

    return app


app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run("fritz_local_brain.app:app", host=settings.api_host, port=settings.api_port)


if __name__ == "__main__":
    main()
