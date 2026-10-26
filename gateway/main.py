"""Main FastAPI application factory and entry point for Llama Gateway."""

from __future__ import annotations

from contextlib import asynccontextmanager
import logging
import os
import sys
from typing import Optional
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

from gateway.config import AppConfig, load_config
from gateway.proxy import ProxyHandler
from gateway.routers import discovery, mcp, openai, status
from gateway.wake_manager import WakeManager

# Configure logging format
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    stream=sys.stdout,
)
logger = logging.getLogger("llama_gateway")


def create_app(config: Optional[AppConfig] = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    if config is None:
        config = load_config()

    wake_manager = WakeManager(wake_config=config.wake, mqtt_config=config.mqtt)
    proxy_handler = ProxyHandler(wake_manager=wake_manager)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("Starting Llama Gateway...")
        logger.info("Configured llamacpp backend: %s", config.llamacpp.backend_url)
        logger.info("Configured MCP servers: %d", len(config.mcp_servers))
        logger.info("Configured static models: %d", len(config.models))
        logger.info(
            "MQTT wake broker: %s:%d (topic=%s, tls=%s)",
            config.mqtt.host,
            config.mqtt.port,
            config.mqtt.topic,
            config.mqtt.use_tls,
        )
        if config.mqtt.port == 8883 and not config.mqtt.use_tls:
            logger.warning(
                "WARNING: MQTT broker port is 8883 (standard TLS port) but use_tls is False. "
                "If your broker requires TLS, set 'use_tls: true' in config.yaml."
            )
        yield
        logger.info("Shutting down Llama Gateway...")
        await wake_manager.close()

    app = FastAPI(
        title="Llama Gateway",
        description="Wake-on-request gateway for local AI node and MCP servers",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Store shared state
    app.state.config = config
    app.state.wake_manager = wake_manager
    app.state.proxy_handler = proxy_handler

    # Allow CORS (for Open WebUI or external clients if needed)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Include routers (order matters: discovery first, then specific OpenAI routes, then status, then catch-all mcp)
    app.include_router(discovery.router)
    app.include_router(status.router)
    app.include_router(openai.router)
    app.include_router(mcp.router)

    return app


app = create_app()


def run() -> None:
    """Entry point for running the gateway server."""
    config_path = os.environ.get("GATEWAY_CONFIG_PATH", "config.yaml")
    cfg = load_config(config_path)
    logger.info("Running Gateway on %s:%d", cfg.server.host, cfg.server.port)
    uvicorn.run(
        "gateway.main:app",
        host=cfg.server.host,
        port=cfg.server.port,
        reload=False,
    )


if __name__ == "__main__":
    run()
