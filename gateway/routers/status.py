"""Gateway status endpoint reporting backend reachability and activity timestamps."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict
from fastapi import APIRouter, Request

from gateway.config import AppConfig
from gateway.wake_manager import WakeManager

logger = logging.getLogger("llama_gateway.status")

router = APIRouter()


@router.get("/gateway/status", tags=["Status"])
async def get_gateway_status(request: Request) -> Dict[str, Any]:
    """
    Returns the current status of the gateway:
    - reachability of each configured backend
    - timestamp of last recorded activity
    - seconds elapsed since last activity
    - whether a wake-up process is currently active
    """
    config: AppConfig = request.app.state.config
    wake_manager: WakeManager = request.app.state.wake_manager

    # Build list of backends to check
    backends = {"llamacpp": config.llamacpp.backend_url}
    for mcp in config.mcp_servers:
        backends[f"mcp_{mcp.name}"] = mcp.backend_url

    status_data = wake_manager.get_status_info(backends)

    # Concurrently perform quick health checks
    health_tasks = {}
    health_tasks["llamacpp"] = wake_manager.check_health(
        config.llamacpp.backend_url,
        config.llamacpp.health_path,
    )
    for mcp in config.mcp_servers:
        mcp_headers = None
        if mcp.bearer_token:
            token = mcp.bearer_token.strip()
            auth_val = token if token.lower().startswith("bearer ") else f"Bearer {token}"
            mcp_headers = {"Authorization": auth_val}
        health_tasks[f"mcp_{mcp.name}"] = wake_manager.check_health(
            mcp.backend_url,
            mcp.health_path,
            headers=mcp_headers,
        )

    results = await asyncio.gather(*health_tasks.values(), return_exceptions=True)
    reachability = {}
    for key, is_reachable in zip(health_tasks.keys(), results):
        reachability[key] = bool(is_reachable) if isinstance(is_reachable, bool) else False

    status_data["reachability"] = reachability
    return status_data
