"""Papertrail FastAPI application factory.

Run locally with::

    uvicorn api.main:app --host 127.0.0.1 --port 8000

Single process, bound to localhost; no auth (local single-user tool). The built
React frontend will later be mounted as static files at ``/``.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.routers import config, media, viz


def create_app() -> FastAPI:
    app = FastAPI(title="Papertrail API", version="0.1.0")
    app.include_router(config.router)
    app.include_router(viz.router)
    app.include_router(media.router)

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
