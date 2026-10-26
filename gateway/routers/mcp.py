"""MCP server proxy routes with wake-on-request and parameter transformation."""

from __future__ import annotations

import json
import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from gateway.config import AppConfig
from gateway.mcp_transform import get_sanitized_server_tools, transform_mcp_payload
from gateway.proxy import ProxyHandler

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


@router.api_route(
    "/mcp/{server_name}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    tags=["MCP Proxy"],
)
async def proxy_mcp_request(server_name: str, path: str, request: Request) -> Response:
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

    return await proxy_handler.forward_request(
        request=request,
        backend_url=mcp_config.backend_url,
        path=f"/{path.lstrip('/')}" if path else "/",
        health_path=mcp_config.health_path,
        override_body=transformed_body,
        extra_headers=extra_headers if extra_headers else None,
    )
