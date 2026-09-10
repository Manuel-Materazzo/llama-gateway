"""OpenAI-compatible operational routes proxied to llama.cpp backend."""

from __future__ import annotations

import logging
from fastapi import APIRouter, Request, Response
from gateway.config import AppConfig
from gateway.proxy import ProxyHandler

logger = logging.getLogger("llama_gateway.openai")

router = APIRouter()


@router.post("/v1/chat/completions", tags=["OpenAI"])
@router.post("/v1/completions", tags=["OpenAI"])
@router.post("/v1/embeddings", tags=["OpenAI"])
async def openai_completions(request: Request) -> Response:
    """
    Proxy OpenAI operational requests to llama.cpp backend with wake-on-request.
    Supports regular JSON responses and SSE streaming chunks.
    """
    config: AppConfig = request.app.state.config
    proxy_handler: ProxyHandler = request.app.state.proxy_handler

    backend_url = config.llamacpp.backend_url
    health_path = config.llamacpp.health_path
    path = request.url.path

    return await proxy_handler.forward_request(
        request=request,
        backend_url=backend_url,
        path=path,
        health_path=health_path,
    )


@router.api_route("/v1/{path:path}", methods=["GET", "POST", "PUT", "DELETE"], tags=["OpenAI Pass-Through"])
async def openai_catch_all(path: str, request: Request) -> Response:
    """
    Catch-all proxy for any other /v1/... endpoints on llama.cpp (e.g. /v1/slots, /v1/tokenize).
    """
    config: AppConfig = request.app.state.config
    proxy_handler: ProxyHandler = request.app.state.proxy_handler

    backend_url = config.llamacpp.backend_url
    health_path = config.llamacpp.health_path

    return await proxy_handler.forward_request(
        request=request,
        backend_url=backend_url,
        path=f"/v1/{path}",
        health_path=health_path,
    )
