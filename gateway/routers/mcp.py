"""MCP server proxy routes with wake-on-request and parameter transformation."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from gateway.config import AppConfig
from gateway.mcp_discovery import sync_mcp_server
from gateway.mcp_transform import get_sanitized_server_tools, transform_mcp_payload
from gateway.proxy import ProxyHandler
from gateway.wake_manager import WakeManager

logger = logging.getLogger("llama_gateway.mcp")

router = APIRouter()


def extract_tool_name_from_path(path: str) -> Optional[str]:
    """Extract tool name if path matches patterns like tools/{name}/call or {name}."""
    parts = [p for p in path.strip("/").split("/") if p]
    if len(parts) == 3 and parts[0] == "tools" and parts[2] == "call":
        return parts[1]
    if len(parts) == 2 and parts[1] == "call":
        return parts[0]
    return None


@router.post("/mcp/{server_name}/sync", tags=["MCP Discovery"])
@router.post("/mcp/{server_name}/discover", tags=["MCP Discovery"])
async def sync_mcp_server_endpoint(
    server_name: str,
    request: Request,
    force: bool = False,
) -> Dict[str, Any]:
    """
    Auto-discover and synchronize tools from upstream MCP server.
    If server.auto_discover is true (or ?force=true), queries upstream MCP server,
    merges schemas, updates config.yaml directly, and reloads in-memory tools.
    """
    config: AppConfig = request.app.state.config
    wake_manager: WakeManager = request.app.state.wake_manager

    mcp_config = next((s for s in config.mcp_servers if s.name == server_name), None)
    if not mcp_config:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' is not configured.",
        )

    if not mcp_config.auto_discover and not force:
        raise HTTPException(
            status_code=400,
            detail=(
                f"MCP server '{server_name}' has auto_discover=false. "
                "Enable 'auto_discover: true' in config.yaml or pass ?force=true to update."
            ),
        )

    config_path = getattr(config, "config_path", None)
    try:
        tools = await sync_mcp_server(
            config=config,
            server_name=server_name,
            config_path=config_path,
            wake_manager=wake_manager,
            force=force,
        )
        return {
            "status": "ok",
            "server": server_name,
            "auto_discover": mcp_config.auto_discover,
            "tools_count": len(tools),
            "tools": tools,
        }
    except Exception as e:
        logger.error("Failed to sync MCP server '%s': %s", server_name, e)
        raise HTTPException(
            status_code=502,
            detail=f"Failed to sync MCP server '{server_name}': {str(e)}",
        )


@router.api_route(
    "/mcp/{server_name}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    tags=["MCP Proxy"],
)
@router.api_route(
    "/mcp/{server_name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    tags=["MCP Proxy"],
)
async def proxy_mcp_request(
    server_name: str,
    request: Request,
    path: str = "",
) -> Response:
    """
    Proxy operational calls to specific MCP servers with wake-on-request
    and automatic injection of hidden/default parameters.
    """
    config: AppConfig = request.app.state.config
    proxy_handler: ProxyHandler = request.app.state.proxy_handler

    mcp_config = next((s for s in config.mcp_servers if s.name == server_name), None)
    if not mcp_config:
        raise HTTPException(
            status_code=404,
            detail=f"MCP server '{server_name}' is not configured.",
        )

    server_dict = mcp_config.model_dump()
    body_bytes = await request.body()

    # Intercept static JSON-RPC discovery methods (e.g. tools/list) so we don't wake the node for catalog exploration
    if request.method == "POST" and body_bytes:
        try:
            json_body = json.loads(body_bytes.decode("utf-8"))
            if isinstance(json_body, dict) and json_body.get("method") in ("tools/list", "tool/list"):
                # If static tools are configured, return sanitized static tools schema immediately
                sanitized_tools = get_sanitized_server_tools(server_dict)
                if sanitized_tools:
                    req_id = json_body.get("id", 1)
                    return JSONResponse(
                        status_code=200,
                        content={
                            "jsonrpc": "2.0",
                            "id": req_id,
                            "result": {
                                "tools": sanitized_tools,
                            },
                        },
                    )
        except Exception:
            pass

    path_tool_name = extract_tool_name_from_path(path)
    transformed_body = transform_mcp_payload(server_dict, body_bytes, path_tool_name=path_tool_name)

    extra_headers: Dict[str, str] = {}
    if mcp_config.bearer_token:
        token = mcp_config.bearer_token.strip()
        auth_val = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        extra_headers["Authorization"] = auth_val

    # Ensure Accept header includes text/event-stream for Streamable HTTP servers
    req_accept = request.headers.get("accept", "")
    if "text/event-stream" not in req_accept:
        if req_accept:
            extra_headers["Accept"] = f"{req_accept}, text/event-stream"
        else:
            extra_headers["Accept"] = "application/json, text/event-stream"

    # Opportunistic sync: if auto_discover is enabled and tools are not yet cached/populated,
    # trigger background sync since the backend is being contacted.
    if mcp_config.auto_discover and not mcp_config.tools:
        config_path = getattr(config, "config_path", None)
        asyncio.create_task(
            sync_mcp_server(config, server_name, config_path=config_path, wake_manager=None)
        )

    return await proxy_handler.forward_request(
        request=request,
        backend_url=mcp_config.backend_url,
        path=f"/{path.lstrip('/')}" if path else "",
        health_path=mcp_config.health_path,
        override_body=transformed_body,
        extra_headers=extra_headers if extra_headers else None,
    )
