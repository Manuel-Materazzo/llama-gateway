"""Web and API services gateway router with Onrender-style waiting page and reverse proxying."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse

from gateway.config import AppConfig, WebServiceConfig
from gateway.proxy import ProxyHandler
from gateway.waiting_page import render_services_catalog, render_waiting_page
from gateway.wake_manager import WakeManager

logger = logging.getLogger("llama_gateway.services")


def _get_service_auth_headers(service: WebServiceConfig) -> Optional[Dict[str, str]]:
    """Format authorization header if bearer_token is configured."""
    if not service.bearer_token:
        return None
    token = service.bearer_token.strip()
    auth_val = token if token.lower().startswith("bearer ") else f"Bearer {token}"
    return {"Authorization": auth_val}


def _find_service(config: AppConfig, service_name: str) -> Optional[WebServiceConfig]:
    """Find a configured service by its name."""
    for svc in config.services:
        if svc.name == service_name:
            return svc
    return None


async def handle_service_request(
    service: WebServiceConfig,
    subpath: str,
    request: Request,
) -> Response:
    """
    Core handler for a web/API service:
    - If browser (Accept: text/html) and server is offline:
        Initiates background wake and serves Onrender-style waiting page (HTTP 200).
    - If API / non-browser:
        Waits for server to wake (debounced) and forwards/redirects request.
    - If server is healthy:
        In proxy mode: transparently reverse proxies request.
        In redirect mode: HTTP 307 redirects to target URL.
    """
    config: AppConfig = request.app.state.config
    wake_manager: WakeManager = request.app.state.wake_manager
    proxy_handler: ProxyHandler = request.app.state.proxy_handler

    prefix = service.effective_path_prefix
    clean_subpath = subpath.lstrip("/")

    # If accessing the exact prefix without trailing slash in a browser, redirect to prefix/
    # to ensure relative links and assets resolve properly.
    raw_path = request.url.path
    if (raw_path == prefix or raw_path == f"/services/{service.name}") and not raw_path.endswith("/"):
        if request.method in ("GET", "HEAD") and "text/html" in request.headers.get("accept", ""):
            redirect_to = f"{raw_path}/"
            if request.url.query:
                redirect_to = f"{redirect_to}?{request.url.query}"
            return RedirectResponse(url=redirect_to, status_code=307)

    is_browser = request.method in ("GET", "HEAD") and "text/html" in request.headers.get("accept", "")
    auth_headers = _get_service_auth_headers(service)

    # 1. Quick health check
    is_healthy = await wake_manager.check_health(
        backend_url=service.backend_url,
        health_path=service.health_path,
        headers=auth_headers,
    )

    # 2. Offline handling
    if not is_healthy:
        if is_browser:
            logger.info(
                "Browser request to sleeping service '%s' [%s]. Dispatching wake and serving waiting page.",
                service.name,
                request.url.path,
            )
            # Trigger background wake if not already running
            asyncio.create_task(
                wake_manager.ensure_awake(
                    backend_url=service.backend_url,
                    health_path=service.health_path,
                    headers=auth_headers,
                    wake_config=service.wake,
                    wake_node=service.should_wake_node,
                )
            )

            # Determine client redirect destination when service wakes
            if service.mode == "redirect":
                base_target = (service.redirect_url or service.backend_url).rstrip("/")
                dest_url = f"{base_target}/{clean_subpath}" if clean_subpath else base_target
                if request.url.query:
                    dest_url = f"{dest_url}?{request.url.query}"
            else:
                dest_url = str(request.url)

            waiting_html = render_waiting_page(
                service=service,
                path_prefix=prefix,
                target_redirect_url=dest_url,
                events_url=f"{prefix}/_gateway/events",
                status_url=f"{prefix}/_gateway/status",
                wake_url=f"{prefix}/_gateway/wake",
                max_timeout=wake_manager.wake_config.max_timeout,
            )
            return HTMLResponse(content=waiting_html, status_code=200)
        else:
            # API / non-browser: wait for wake to complete
            logger.info(
                "API request to sleeping service '%s' [%s %s]. Waiting for wake-up...",
                service.name,
                request.method,
                request.url.path,
            )
            success, err_msg = await wake_manager.ensure_awake(
                backend_url=service.backend_url,
                health_path=service.health_path,
                headers=auth_headers,
                wake_config=service.wake,
                wake_node=service.should_wake_node,
            )
            if not success:
                logger.error("Wake-on-request failed for service '%s': %s", service.name, err_msg)
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": {
                            "message": err_msg or f"Service '{service.name}' failed to wake up.",
                            "type": "gateway_timeout",
                            "code": 503,
                        }
                    },
                )

    # 3. Server is healthy: proxy or redirect
    if service.should_wake_node:
        wake_manager.record_activity()
    if service.mode == "redirect":
        base_target = (service.redirect_url or service.backend_url).rstrip("/")
        dest_url = f"{base_target}/{clean_subpath}" if clean_subpath else base_target
        if request.url.query:
            dest_url = f"{dest_url}?{request.url.query}"
        logger.info("Redirecting client to service '%s': %s", service.name, dest_url)
        return RedirectResponse(url=dest_url, status_code=307)

    # Proxy mode: reverse proxy to backend URL
    extra_headers: Dict[str, str] = {
        "X-Forwarded-Host": request.headers.get("host", ""),
        "X-Forwarded-Proto": request.url.scheme,
        "X-Forwarded-Prefix": prefix,
    }
    client_ip = request.client.host if request.client else None
    if client_ip:
        extra_headers["X-Forwarded-For"] = client_ip

    if auth_headers:
        extra_headers.update(auth_headers)

    return await proxy_handler.forward_request(
        request=request,
        backend_url=service.backend_url,
        path=clean_subpath,
        health_path=service.health_path,
        extra_headers=extra_headers,
    )


def create_services_router(config: AppConfig) -> APIRouter:
    """Create and configure the FastAPI router for all web & API services."""
    router = APIRouter(tags=["Services Gateway"])

    # 1. Services Catalog endpoint
    @router.get("/services")
    async def get_services_catalog(request: Request) -> Response:
        """List all configured web & API services (HTML dashboard for browsers, JSON for API)."""
        accept = request.headers.get("accept", "")
        if "text/html" in accept:
            return HTMLResponse(render_services_catalog(config.services))

        wake_manager: WakeManager = request.app.state.wake_manager
        catalog = []
        for svc in config.services:
            auth_headers = _get_service_auth_headers(svc)
            is_healthy = await wake_manager.check_health(
                svc.backend_url,
                svc.health_path,
                headers=auth_headers,
            )
            catalog.append({
                "name": svc.name,
                "title": svc.title or svc.name.capitalize(),
                "description": svc.description,
                "path": svc.effective_path_prefix,
                "mode": svc.mode,
                "wake_node": svc.should_wake_node,
                "backend_url": svc.backend_url,
                "healthy": is_healthy,
                "icon": svc.icon or "⚡",
            })
        return JSONResponse(content={"services": catalog})

    def _register_service_endpoints(svc: WebServiceConfig, route_prefix: str) -> None:
        """Register gateway helper routes and proxy catch-all routes for a specific prefix."""
        norm_prefix = "/" + route_prefix.strip("/")

        # Status endpoint
        @router.get(f"{norm_prefix}/_gateway/status")
        async def service_status(request: Request) -> Dict[str, Any]:
            wake_manager: WakeManager = request.app.state.wake_manager
            auth_headers = _get_service_auth_headers(svc)
            is_healthy = await wake_manager.check_health(
                svc.backend_url,
                svc.health_path,
                headers=auth_headers,
            )
            session = wake_manager.get_wake_session(svc.backend_url)
            return {
                "service": svc.name,
                "title": svc.title or svc.name,
                "healthy": is_healthy,
                "waking": session is not None and not session.event.is_set(),
                "mode": svc.mode,
                "wake_node": svc.should_wake_node,
                "backend_url": svc.backend_url,
                "redirect_url": svc.redirect_url or svc.backend_url,
                "phase": session.phase if session else ("ready" if is_healthy else "offline"),
            }

        # Events SSE endpoint
        @router.get(f"{norm_prefix}/_gateway/events")
        async def service_events(request: Request) -> StreamingResponse:
            wake_manager: WakeManager = request.app.state.wake_manager
            auth_headers = _get_service_auth_headers(svc)

            async def sse_stream():
                async for event in wake_manager.subscribe_wake_events(
                    backend_url=svc.backend_url,
                    health_path=svc.health_path,
                    headers=auth_headers,
                ):
                    yield f"data: {json.dumps(event)}\n\n".encode("utf-8")

            return StreamingResponse(
                sse_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                },
            )

        # Explicit wake trigger endpoint
        @router.post(f"{norm_prefix}/_gateway/wake")
        async def trigger_service_wake(request: Request) -> Dict[str, Any]:
            wake_manager: WakeManager = request.app.state.wake_manager
            auth_headers = _get_service_auth_headers(svc)
            asyncio.create_task(
                wake_manager.ensure_awake(
                    backend_url=svc.backend_url,
                    health_path=svc.health_path,
                    headers=auth_headers,
                    wake_config=svc.wake,
                    wake_node=svc.should_wake_node,
                )
            )
            return {"status": "waking", "service": svc.name, "wake_node": svc.should_wake_node}

        # Direct waiting page preview
        @router.get(f"{norm_prefix}/_gateway/wait")
        async def view_waiting_page(request: Request) -> HTMLResponse:
            wake_manager: WakeManager = request.app.state.wake_manager
            target_dest = svc.redirect_url or svc.backend_url if svc.mode == "redirect" else norm_prefix
            html_page = render_waiting_page(
                service=svc,
                path_prefix=norm_prefix,
                target_redirect_url=target_dest,
                events_url=f"{norm_prefix}/_gateway/events",
                status_url=f"{norm_prefix}/_gateway/status",
                wake_url=f"{norm_prefix}/_gateway/wake",
                max_timeout=wake_manager.wake_config.max_timeout,
            )
            return HTMLResponse(content=html_page)

        # Operational proxy routes: root and subpaths
        @router.api_route(
            norm_prefix,
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        )
        async def service_root(request: Request) -> Response:
            return await handle_service_request(service=svc, subpath="", request=request)

        @router.api_route(
            f"{norm_prefix}/",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        )
        async def service_root_slash(request: Request) -> Response:
            return await handle_service_request(service=svc, subpath="", request=request)

        @router.api_route(
            f"{norm_prefix}/{{subpath:path}}",
            methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
        )
        async def service_subpath(subpath: str, request: Request) -> Response:
            # Exclude internal _gateway endpoints if matched
            if subpath.startswith("_gateway"):
                raise HTTPException(status_code=404, detail="Not found")
            return await handle_service_request(service=svc, subpath=subpath, request=request)

    # Register endpoints for all configured services
    for svc in config.services:
        primary_prefix = svc.effective_path_prefix
        _register_service_endpoints(svc, primary_prefix)

        # If primary_prefix is a custom prefix (e.g. /jenkins), also register /services/{name} as alias
        alias_prefix = f"/services/{svc.name}"
        if primary_prefix != alias_prefix:
            _register_service_endpoints(svc, alias_prefix)

    return router


# Module-level router instance
router = create_services_router(AppConfig())
