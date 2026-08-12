"""FastAPI application exposing Docker Engine API routes.

**Experimental / not for production.** The supported daemon is aiohttp
(``server.py`` → ``aio_server.py``). This FastAPI app may lag behind aio
routes (e.g. restart/rename/archive/events). Prefer aiohttp for CLI use.
"""

from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .api import containers, exec as exec_api, images, system
from .backend.runtime import (
    DEFAULT_KUBE_CONTEXT,
    DEFAULT_IMAGE,
    resolve_kubeconfig,
    resolve_namespace,
)
from .backend.store import ContainerStore

_VERSION_RE = re.compile(r"^/v\d+\.\d+(/.*)?$")


class StripApiVersionMiddleware:
    """Rewrite /v1.44/foo -> /foo before routing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            path = scope.get("path", "")
            match = _VERSION_RE.match(path)
            if match:
                scope = dict(scope)
                scope["path"] = match.group(1) or "/"
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.store = ContainerStore()
    kubeconfig = resolve_kubeconfig()
    kube_context = os.getenv("DOCKER_RT_KUBE_CONTEXT", DEFAULT_KUBE_CONTEXT)
    app.state.kubeconfig = kubeconfig
    app.state.kube_context = kube_context
    app.state.namespace = resolve_namespace(
        kubeconfig=kubeconfig, kube_context=kube_context
    )
    app.state.default_image = os.getenv("DOCKER_RT_DEFAULT_IMAGE", DEFAULT_IMAGE)
    yield
    store: ContainerStore = app.state.store
    for record in list(store.list(all_containers=True)):
        if record.kube_env is not None:
            try:
                record.kube_env.cleanup()
            except Exception:
                pass


def create_app() -> FastAPI:
    app = FastAPI(
        title="docker-rt",
        description="Docker Engine API facade over Kubernetes sandboxes",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.include_router(system.router)
    app.include_router(containers.router)
    app.include_router(images.router)
    app.include_router(exec_api.router)

    @app.middleware("http")
    async def docker_error_headers(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers.setdefault("Api-Version", system.API_VERSION)
        response.headers.setdefault("Docker-Experimental", "false")
        response.headers.setdefault("Ostype", "linux")
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    # Outermost: path rewrite before routing.
    app.add_middleware(StripApiVersionMiddleware)
    return app


app = create_app()
