"""Papertrail FastAPI application factory.

Run locally with::

    uvicorn api.main:app --host 127.0.0.1 --port 8000

Single process, bound to localhost; no auth (local single-user tool). The built
React frontend will later be mounted as static files at ``/``.
"""

from __future__ import annotations

from env import load_env

load_env()  # API keys: process env, then <repo>/.env, then the shared user-level file (see env.py)

from fastapi import FastAPI

from api.routers import brands, config, curate, dev, ingest, jobs, media, review, viz, workshop
from api.schemas import Health


def create_app() -> FastAPI:
    app = FastAPI(title="Papertrail API", version="0.1.0")
    app.include_router(config.router)
    app.include_router(brands.router)
    app.include_router(curate.router)
    app.include_router(workshop.router)
    app.include_router(viz.router)
    app.include_router(media.router)
    app.include_router(review.router)
    app.include_router(ingest.router)
    app.include_router(jobs.router)
    app.include_router(dev.router)

    @app.get("/api/health", response_model=Health)
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
