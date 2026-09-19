"""Papertrail FastAPI application factory.

Run locally with::

    uvicorn api.main:app --host 127.0.0.1 --port 8000

Bound to localhost, no auth (a local single-user tool); the web app in ``frontend/`` talks to
it (``npm --prefix frontend run dev``), and only pages on this machine are served (see
``only_this_machine``).
"""

from __future__ import annotations

from env import load_env

load_env()  # API keys: process env, then <repo>/.env, then the shared user-level file (see env.py)

from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from api.routers import brands, config, curate, dev, ingest, jobs, media, review, viz, workshop
from api.schemas import Health
from document_files import FileInUse


#: The names this machine answers to. The API serves only them: see ``only_this_machine``.
LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def _host(value: str) -> str:
    return (urlsplit(f"//{value}").hostname or "").lower()


def create_app() -> FastAPI:
    app = FastAPI(title="Papertrail API", version="0.1.0")

    @app.middleware("http")
    async def only_this_machine(request: Request, call_next):
        """Serve only pages running on this machine.

        Listening on 127.0.0.1 keeps other computers out, but any website open in the browser runs on
        this machine too. Three checks keep those out:
        - Host must be a local name. A site whose domain is made to resolve to 127.0.0.1 (DNS rebinding)
          still sends its own name here.
        - Origin, when there is one, must be a local page: a script on another site always sends its own.
        - Sec-Fetch-Site, which the browser sets, must not say the request came from another site (a
          form or an image tag on someone else's page).
        """
        refused = None
        if _host(request.headers.get("host", "")) not in LOCAL_HOSTS:
            refused = "the Host isn't this machine"
        elif (origin := request.headers.get("origin")) is not None and _host(urlsplit(origin).netloc) not in LOCAL_HOSTS:
            refused = "the request comes from another site"
        elif request.headers.get("sec-fetch-site") == "cross-site":
            refused = "the request comes from another site"
        if refused:
            return JSONResponse(status_code=403, content={"detail": f"Refused: {refused}."})
        return await call_next(request)
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

    # Moving a document refuses before anything moves if a file is open elsewhere or its new name is taken
    # (document_files); either way the archive is as it was, and the message says what to do.
    @app.exception_handler(FileInUse)
    @app.exception_handler(FileExistsError)
    async def refused_move(_request: Request, exc: OSError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.get("/api/health", response_model=Health)
    def health() -> dict:
        return {"status": "ok"}

    return app


app = create_app()
