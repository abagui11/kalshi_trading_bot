"""FastAPI app — thin Kalshi paper performance dashboard."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import paper
from dashboard import data

_PKG_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(title="Kalshi 15m Paper Bot", docs_url=None, redoc_url=None)
    paper.init_db()

    templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))
    static_dir = _PKG_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request,
            "index.html",
            data.dashboard_context(),
        )

    @app.get("/api/status")
    async def api_status() -> dict:
        return data.get_status_payload()

    @app.get("/api/performance")
    async def api_performance() -> dict:
        return data.get_performance_payload()

    return app
