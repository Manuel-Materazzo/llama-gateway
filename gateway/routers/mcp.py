"""MCP server proxy routes with wake-on-request."""

from __future__ import annotations

import logging
from fastapi import APIRouter, HTTPException, Request, Response
from gateway.config import AppConfig
from gateway.proxy import ProxyHandler

logger = logging.getLogger("llama_gateway.mcp")

router = APIRouter()


@router.api_route(
    "/mcp/{server_name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    tags=["MCP Proxy"],
)
async def proxy_mcp_request(server_name: str, path: str, request: Request) -> Response:
    """
    Proxy operational calls to specific MCP servers with wake-on-request.
    """
    config: AppConfig = request.app.state.config
    proxy_handler: ProxyHandler = request.app.state.proxy_handler

    mcp_config = next((s for s in config.mcp_servers if s.name == server_name), None)
    if not mcp_config:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' is not configured.",
        )

    return await proxy_handler.forward_request(
        request=request,
        backend_url=mcp_config.backend_url,
        path=f"/{path.lstrip('/')}" if path else "/",
        health_path=mcp_config.health_path,
    )
