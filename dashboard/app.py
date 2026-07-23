"""FastAPI app — multi-bot Kalshi paper performance + decision journal."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import bot_config
import config
import paper
from dashboard import data
from dashboard.charts import resolve_chart_path

_PKG_DIR = Path(__file__).resolve().parent


def create_app() -> FastAPI:
    app = FastAPI(title="Kalshi 15m Multi-Bot Paper", docs_url=None, redoc_url=None)
    paper.init_db()

    templates = Jinja2Templates(directory=str(_PKG_DIR / "templates"))
    static_dir = _PKG_DIR / "static"
    if static_dir.is_dir():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(
        request: Request,
        filter: str = Query("all", alias="filter"),
        bot: str | None = Query(None),
    ) -> HTMLResponse:
        mode = filter if filter in ("all", "trades", "skips") else "all"
        bot_id = bot if bot in bot_config.ENABLED_BOTS else None
        return templates.TemplateResponse(
            request,
            "index.html",
            data.dashboard_context(filter_mode=mode, bot_id=bot_id),
        )

    @app.get("/api/status")
    async def api_status() -> dict:
        return data.get_status_payload()

    @app.get("/api/bots")
    async def api_bots() -> dict:
        return {"bots": data.get_bots_payload()}

    @app.get("/api/performance")
    async def api_performance(
        bot: str | None = Query(None),
    ) -> dict:
        bot_id = bot if bot in bot_config.ENABLED_BOTS else "control"
        return data.get_performance_payload(bot_id=bot_id)

    @app.get("/api/structure")
    async def api_structure() -> dict:
        return {"assets": data.get_structure_payload()}

    @app.get("/api/journal")
    async def api_journal(
        filter: str = Query("all"),
        limit: int = Query(50, ge=1, le=200),
        bot: str | None = Query(None),
    ) -> dict:
        mode = filter if filter in ("all", "trades", "skips") else "all"
        bot_id = bot if bot in bot_config.ENABLED_BOTS else None
        return {
            "bot_id": bot_id,
            "decisions": data.get_journal_payload(
                limit=limit, filter_mode=mode, bot_id=bot_id
            ),
        }

    @app.get("/api/chart/file/{path:path}")
    async def api_chart_file(path: str) -> FileResponse:
        candidate = (config.CHARTS_DIR / path).resolve()
        resolved = resolve_chart_path(str(candidate))
        if resolved is None:
            raise HTTPException(status_code=404, detail="chart not found")
        return FileResponse(resolved)

    return app
