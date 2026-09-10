"""Discovery routes providing static model catalogs and MCP tool schemas."""

from __future__ import annotations

import logging
from typing import Any, Dict, List
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from gateway.config import AppConfig
from gateway.mcp_transform import get_sanitized_server_tools

logger = logging.getLogger("llama_gateway.discovery")

router = APIRouter()


@router.get("/v1/models", tags=["Discovery"])
async def get_models(request: Request) -> Dict[str, Any]:
    """
    OpenAI-compatible models discovery endpoint.
    Always returns 200 OK with static models list from configuration,
    regardless of backend node state.
    """
    config: AppConfig = request.app.state.config
    models_data = [m.model_dump() for m in config.models]
    return {
        "object": "list",
        "data": models_data,
    }


@router.get("/mcp/{server_name}/discovery", tags=["MCP Discovery"])
@router.get("/mcp/{server_name}/tools", tags=["MCP Discovery"])
@router.get("/mcp/{server_name}/tools/list", tags=["MCP Discovery"])
async def get_mcp_static_tools(server_name: str, request: Request) -> Dict[str, Any]:
    """
    Static discovery endpoint for MCP server tools.
    Always returns 200 OK with the sanitized schema from configuration (hidden fields stripped).
    """
    config: AppConfig = request.app.state.config
    mcp_config = next((s for s in config.mcp_servers if s.name == server_name), None)

    if not mcp_config:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' not configured in gateway.",
        )

    if mcp_config.static_discovery:
        return mcp_config.static_discovery

    sanitized_tools = get_sanitized_server_tools(mcp_config.model_dump())
    return {
        "server": server_name,
        "tools": sanitized_tools,
    }
