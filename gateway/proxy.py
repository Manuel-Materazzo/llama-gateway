"""Reverse proxy with streaming support and wake-on-request integration."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import AsyncGenerator, Dict, Optional
from fastapi import Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
import httpx

from gateway.wake_manager import WakeManager

logger = logging.getLogger("llama_gateway.proxy")

# Headers to filter out when forwarding requests/responses
EXCLUDED_REQUEST_HEADERS = {"host", "content-length", "connection"}
EXCLUDED_RESPONSE_HEADERS = {"content-length", "connection", "transfer-encoding", "content-encoding"}


def is_streaming_request(request: Request, body_bytes: bytes) -> bool:
    """Check if the incoming request expects a streaming response."""
    accept_header = request.headers.get("accept", "").lower()
    if "text/event-stream" in accept_header:
        return True

    if body_bytes:
        try:
            data = json.loads(body_bytes.decode("utf-8"))
            if isinstance(data, dict) and data.get("stream") is True:
                return True
        except Exception:
            pass

    return False


def build_forward_headers(request: Request) -> Dict[str, str]:
    """Construct sanitized headers for forwarding to upstream backend."""
    forward_headers = {}
    for key, value in request.headers.items():
        if key.lower() not in EXCLUDED_REQUEST_HEADERS:
            forward_headers[key] = value
    return forward_headers


def build_response_headers(upstream_headers: httpx.Headers) -> Dict[str, str]:
    """Construct sanitized headers to return to client."""
    resp_headers = {}
    for key, value in upstream_headers.items():
        if key.lower() not in EXCLUDED_RESPONSE_HEADERS:
            resp_headers[key] = value
    return resp_headers


class ProxyHandler:
    """Handles forwarding requests to local backends with wake-on-request support."""

    def __init__(self, wake_manager: WakeManager, client_timeout: float = 300.0) -> None:
        self.wake_manager = wake_manager
        self.client_timeout = client_timeout

    async def _stream_with_wake(
        self,
        target_url: str,
        backend_url: str,
        health_path: str,
        method: str,
        headers: Dict[str, str],
        params: Dict[str, str],
        body_bytes: bytes,
    ) -> AsyncGenerator[bytes, None]:
        """
        Streaming generator that handles keep-alive during wake-up,
        then streams the upstream response.
        """
        # Check initial reachability
        is_healthy = await self.wake_manager.check_health(backend_url, health_path)

        if not is_healthy:
            logger.info("Streaming request detected offline backend %s. Starting wake-up with SSE keep-alive...", backend_url)
            # Start wake task
            wake_task = asyncio.create_task(
                self.wake_manager.ensure_awake(backend_url, health_path)
            )

            keep_alive_interval = self.wake_manager.wake_config.keep_alive_interval

            while not wake_task.done():
                # Send SSE comment to keep connection alive
                yield b": keep-alive\n\n"
                try:
                    await asyncio.wait_for(asyncio.shield(wake_task), timeout=keep_alive_interval)
                except asyncio.TimeoutError:
                    continue
                except Exception:
                    break

            success, error_msg = await wake_task
            if not success:
                logger.error("Wake-up failed during streaming: %s", error_msg)
                error_payload = {
                    "error": {
                        "message": error_msg or "Target backend failed to wake up.",
                        "type": "gateway_wake_timeout",
                        "code": 503,
                    }
                }
                yield f"data: {json.dumps(error_payload)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                return

        # Upstream is healthy: stream real response
        logger.info("Forwarding streaming request to %s", target_url)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.client_timeout)) as client:
                async with client.stream(
                    method=method,
                    url=target_url,
                    headers=headers,
                    params=params,
                    content=body_bytes,
                ) as upstream_response:
                    if upstream_response.status_code >= 400:
                        logger.warning("Upstream returned error status %d for %s", upstream_response.status_code, target_url)

                    self.wake_manager.record_activity()
                    async for chunk in upstream_response.aiter_bytes():
                        yield chunk

            # Record activity again after successful stream completion
            self.wake_manager.record_activity()
        except Exception as e:
            logger.error("Error during streaming pass-through to %s: %s", target_url, e, exc_info=True)
            error_payload = {
                "error": {
                    "message": f"Error proxying streaming response: {str(e)}",
                    "type": "gateway_proxy_error",
                    "code": 502,
                }
            }
            yield f"data: {json.dumps(error_payload)}\n\n".encode("utf-8")
            yield b"data: [DONE]\n\n"

    async def forward_request(
        self,
        request: Request,
        backend_url: str,
        path: str,
        health_path: str = "/health",
        override_body: Optional[bytes] = None,
    ) -> Response:
        """Forward an HTTP request to the target backend with wake-on-request."""
        body_bytes = override_body if override_body is not None else await request.body()
        clean_backend = backend_url.rstrip("/")
        clean_path = path.lstrip("/")
        target_url = f"{clean_backend}/{clean_path}" if clean_path else clean_backend

        forward_headers = build_forward_headers(request)
        params = dict(request.query_params)
        is_stream = is_streaming_request(request, body_bytes)

        if is_stream:
            # Handle streaming response with SSE keep-alive support
            return StreamingResponse(
                self._stream_with_wake(
                    target_url=target_url,
                    backend_url=backend_url,
                    health_path=health_path,
                    method=request.method,
                    headers=forward_headers,
                    params=params,
                    body_bytes=body_bytes,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # Standard non-streaming request
        success, error_msg = await self.wake_manager.ensure_awake(backend_url, health_path)
        if not success:
            logger.error("Wake-on-request failed for %s: %s", backend_url, error_msg)
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "message": error_msg or "Backend node is unreachable and failed to wake up.",
                        "type": "gateway_timeout",
                        "code": 503,
                    }
                },
            )

        # Upstream is awake, send request
        logger.info("Forwarding request [%s] %s", request.method, target_url)
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(self.client_timeout)) as client:
                upstream_resp = await client.request(
                    method=request.method,
                    url=target_url,
                    headers=forward_headers,
                    params=params,
                    content=body_bytes,
                )

            # Record activity for successful operational request
            self.wake_manager.record_activity()

            return Response(
                content=upstream_resp.content,
                status_code=upstream_resp.status_code,
                headers=build_response_headers(upstream_resp.headers),
                media_type=upstream_resp.headers.get("content-type"),
            )
        except Exception as e:
            logger.error("Failed to forward request to %s: %s", target_url, e, exc_info=True)
            return JSONResponse(
                status_code=502,
                content={
                    "error": {
                        "message": f"Gateway failed to reach backend: {str(e)}",
                        "type": "gateway_bad_gateway",
                        "code": 502,
                    }
                },
            )
