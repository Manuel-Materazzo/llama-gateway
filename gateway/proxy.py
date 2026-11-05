"""Reverse proxy with streaming support and wake-on-request integration."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional
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


def has_return_progress(request: Request, body_bytes: bytes) -> bool:
    """Check if the incoming request asks for prompt/server progress updates."""
    # Check query params (e.g. ?return_progress=true)
    val = request.query_params.get("return_progress")
    if val is not None and str(val).lower() in ("true", "1", "yes"):
        return True

    # Check custom header (e.g. X-Return-Progress: true)
    hdr = request.headers.get("x-return-progress")
    if hdr is not None and hdr.lower() in ("true", "1", "yes"):
        return True

    # Check JSON payload: root field or nested extra_body (OpenAI SDK pattern)
    if body_bytes:
        try:
            data = json.loads(body_bytes.decode("utf-8"))
            if isinstance(data, dict):
                rp = data.get("return_progress")
                if rp is True or str(rp).lower() in ("true", "1", "yes"):
                    return True
                extra = data.get("extra_body")
                if isinstance(extra, dict):
                    erp = extra.get("return_progress")
                    if erp is True or str(erp).lower() in ("true", "1", "yes"):
                        return True
        except Exception:
            pass

    return False


def extract_model_from_body(body_bytes: bytes, default: str = "local-model") -> str:
    """Extract model name from request body if present."""
    if body_bytes:
        try:
            data = json.loads(body_bytes.decode("utf-8"))
            if isinstance(data, dict) and "model" in data and isinstance(data["model"], str):
                return data["model"]
        except Exception:
            pass
    return default


def format_progress_chunk(
    model: str,
    is_chat: bool,
    stage: str,
    status_msg: str,
    elapsed: float,
    progress_pct: int,
) -> bytes:
    """Format an SSE chat/completion chunk with prompt_progress and server status."""
    time_ms = int(elapsed * 1000)
    progress_data = {
        "total": 100,
        "processed": max(0, min(100, int(progress_pct))),
        "cache": 0,
        "time_ms": time_ms,
    }
    if is_chat:
        chunk = {
            "id": "chatcmpl-gateway-wake",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": None,
                }
            ],
            "stage": stage,
            "status": status_msg,
            "prompt_progress": progress_data,
        }
    else:
        chunk = {
            "id": "cmpl-gateway-wake",
            "object": "text_completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "text": "",
                    "finish_reason": None,
                }
            ],
            "stage": stage,
            "status": status_msg,
            "prompt_progress": progress_data,
        }
    return f"data: {json.dumps(chunk)}\n\n".encode("utf-8")


def build_forward_headers(
    request: Request,
    extra_headers: Optional[Dict[str, str]] = None,
    strip_headers: Optional[List[str]] = None,
) -> Dict[str, str]:
    """Construct sanitized headers for forwarding to upstream backend."""
    forward_headers = {}
    strip_set = {s.lower() for s in (strip_headers or [])}
    for key, value in request.headers.items():
        k_lower = key.lower()
        if k_lower not in EXCLUDED_REQUEST_HEADERS and k_lower not in strip_set:
            forward_headers[key] = value

    if extra_headers:
        for extra_key, extra_value in extra_headers.items():
            # Remove any existing header with the same name case-insensitively
            keys_to_remove = [k for k in forward_headers if k.lower() == extra_key.lower()]
            for k in keys_to_remove:
                del forward_headers[k]
            forward_headers[extra_key] = extra_value

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
        health_headers: Optional[Dict[str, str]] = None,
        return_progress: bool = False,
        model_name: str = "local-model",
        is_chat: bool = True,
    ) -> AsyncGenerator[bytes, None]:
        """
        Streaming generator that handles keep-alive and optional progress updates during wake-up,
        then streams the upstream response.
        """
        # Check initial reachability
        is_healthy = await (
            self.wake_manager.check_health(backend_url, health_path, headers=health_headers)
            if health_headers is not None
            else self.wake_manager.check_health(backend_url, health_path)
        )

        if not is_healthy:
            logger.info(
                "Streaming request detected offline backend %s (return_progress=%s). Starting wake-up...",
                backend_url,
                return_progress,
            )
            wake_start_time = time.time()
            # Start wake task
            wake_task = asyncio.create_task(
                self.wake_manager.ensure_awake(backend_url, health_path, headers=health_headers)
                if health_headers is not None
                else self.wake_manager.ensure_awake(backend_url, health_path)
            )

            keep_alive_interval = self.wake_manager.wake_config.keep_alive_interval
            update_interval = min(keep_alive_interval, 2.0) if return_progress else keep_alive_interval
            estimated_boot = getattr(self.wake_manager.wake_config, "estimated_boot_duration", 45.0)

            # Send initial progress chunk immediately if requested
            if return_progress:
                yield format_progress_chunk(
                    model=model_name,
                    is_chat=is_chat,
                    stage="starting",
                    status_msg="Wake-up sequence initiated...",
                    elapsed=0.0,
                    progress_pct=5,
                )

            while not wake_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(wake_task), timeout=update_interval)
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    break

                if wake_task.done():
                    break

                if return_progress:
                    elapsed = max(0.0, time.time() - wake_start_time)
                    session = self.wake_manager.get_wake_session(backend_url)
                    stage = session.phase if session else "booting"
                    status_msg = session.message if session else f"Waiting for server to boot ({elapsed:.0f}s elapsed)..."
                    progress_pct = min(92, max(5, int(5 + (elapsed / estimated_boot) * 85)))
                    yield format_progress_chunk(
                        model=model_name,
                        is_chat=is_chat,
                        stage=stage,
                        status_msg=status_msg,
                        elapsed=elapsed,
                        progress_pct=progress_pct,
                    )
                else:
                    # Send SSE comment to keep connection alive
                    yield b": keep-alive\n\n"

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

            if return_progress:
                total_elapsed = max(0.0, time.time() - wake_start_time)
                yield format_progress_chunk(
                    model=model_name,
                    is_chat=is_chat,
                    stage="ready",
                    status_msg="Server is ready! Initializing inference...",
                    elapsed=total_elapsed,
                    progress_pct=100,
                )

        # Upstream is healthy: stream real response
        logger.info("Forwarding streaming request to %s", target_url)
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.client_timeout),
                follow_redirects=True,
            ) as client:
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
        extra_headers: Optional[Dict[str, str]] = None,
        strip_headers: Optional[List[str]] = None,
    ) -> Response:
        """Forward an HTTP request to the target backend with wake-on-request."""
        body_bytes = override_body if override_body is not None else await request.body()
        clean_backend = backend_url.rstrip("/")
        clean_path = path.lstrip("/")
        target_url = f"{clean_backend}/{clean_path}" if clean_path else clean_backend

        forward_headers = build_forward_headers(
            request,
            extra_headers=extra_headers,
            strip_headers=strip_headers,
        )
        params = dict(request.query_params)
        is_stream = is_streaming_request(request, body_bytes)
        return_progress = has_return_progress(request, body_bytes)
        model_name = extract_model_from_body(body_bytes, default="local-model")
        clean_path_lower = path.lower()
        is_chat = not (clean_path_lower.endswith("/completions") and not clean_path_lower.endswith("/chat/completions"))

        # If return_progress requested, ensure upstream llama.cpp also receives return_progress: true in JSON body
        forward_body = body_bytes
        if return_progress and forward_body:
            try:
                data = json.loads(forward_body.decode("utf-8"))
                if isinstance(data, dict) and not data.get("return_progress"):
                    data["return_progress"] = True
                    forward_body = json.dumps(data).encode("utf-8")
            except Exception:
                pass

        health_headers = None
        if extra_headers:
            health_headers = {k: v for k, v in extra_headers.items() if k.lower() != "accept"}
        elif "authorization" in forward_headers:
            health_headers = {"authorization": forward_headers["authorization"]}

        if is_stream:
            # Handle streaming response with SSE keep-alive and return_progress support
            return StreamingResponse(
                self._stream_with_wake(
                    target_url=target_url,
                    backend_url=backend_url,
                    health_path=health_path,
                    method=request.method,
                    headers=forward_headers,
                    params=params,
                    body_bytes=forward_body,
                    health_headers=health_headers,
                    return_progress=return_progress,
                    model_name=model_name,
                    is_chat=is_chat,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # Standard non-streaming request
        success, error_msg = await (
            self.wake_manager.ensure_awake(backend_url, health_path, headers=health_headers)
            if health_headers is not None
            else self.wake_manager.ensure_awake(backend_url, health_path)
        )
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
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(self.client_timeout),
                follow_redirects=True,
            ) as client:
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
