"""MCP server auto-discovery and YAML synchronization utilities."""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import logging
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional
import httpx
import yaml

from gateway.config import AppConfig, MCPServerConfig, load_config
from gateway.wake_manager import WakeManager

logger = logging.getLogger("llama_gateway.mcp_discovery")


def _parse_response_json(resp: httpx.Response) -> Optional[Dict[str, Any]]:
    """Parse JSON or SSE text/event-stream from an MCP response."""
    try:
        return resp.json()
    except Exception:
        pass

    text = resp.text
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            data_str = line[5:].strip()
            if data_str and data_str != "[DONE]":
                try:
                    parsed = json.loads(data_str)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    continue
    return None


def _extract_tools_from_response(data: Any) -> Optional[List[Dict[str, Any]]]:
    """Extract tools list from parsed response payload."""
    if not isinstance(data, dict):
        return None
    tools = None
    if "result" in data and isinstance(data["result"], dict):
        tools = data["result"].get("tools")
    elif "tools" in data:
        tools = data.get("tools")
    elif isinstance(data, list):
        tools = data
    if isinstance(tools, list):
        return _normalize_tools(tools)
    return None


async def fetch_tools_from_mcp_server(
    backend_url: str,
    bearer_token: Optional[str] = None,
    timeout: float = 10.0,
    client: Optional[httpx.AsyncClient] = None,
) -> List[Dict[str, Any]]:
    """
    Query an upstream MCP server for its list of exposed tools.
    Supports standard JSON-RPC 2.0 (tools/list), Streamable HTTP MCP handshake
    (initialize -> notifications/initialized -> tools/list), and REST fallbacks.
    """
    clean_base = backend_url.rstrip("/")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if bearer_token:
        token = bearer_token.strip()
        auth_val = token if token.lower().startswith("bearer ") else f"Bearer {token}"
        headers["Authorization"] = auth_val

    # Candidate endpoints for JSON-RPC POST (clean_base tried first, with and without trailing slash)
    post_candidate_urls: List[str] = [clean_base, f"{clean_base}/"]
    if not clean_base.endswith("/mcp"):
        post_candidate_urls.extend([f"{clean_base}/mcp", f"{clean_base}/mcp/"])
    if not clean_base.endswith("/tools/list"):
        post_candidate_urls.append(f"{clean_base}/tools/list")
    # Deduplicate while preserving order
    post_candidate_urls = list(dict.fromkeys(post_candidate_urls))

    close_client = False
    if client is None:
        client = httpx.AsyncClient(timeout=timeout, follow_redirects=True)
        close_client = True

    try:
        last_error = None

        # 1. Try JSON-RPC POST calls
        for url in post_candidate_urls:
            # 1a. Try direct tools/list call
            try:
                jsonrpc_payload = {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/list",
                    "params": {},
                }
                resp = await client.post(
                    url,
                    json=jsonrpc_payload,
                    headers=headers,
                )
                if resp.status_code == 200:
                    data = _parse_response_json(resp)
                    tools = _extract_tools_from_response(data)
                    if tools is not None:
                        logger.info("Discovered %d tools from %s via JSON-RPC", len(tools), url)
                        return tools
                else:
                    last_error = f"HTTP {resp.status_code} from {url}"
            except Exception as e:
                last_error = e

            # 1b. Try MCP initialize handshake (Streamable HTTP / standard MCP lifecycle)
            for proto_ver in ["2025-03-26", "2024-11-05"]:
                try:
                    init_payload = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": proto_ver,
                            "capabilities": {},
                            "clientInfo": {"name": "llama-gateway", "version": "1.0"},
                        },
                    }
                    init_resp = await client.post(
                        url,
                        json=init_payload,
                        headers=headers,
                    )
                    if init_resp.status_code == 200:
                        init_data = _parse_response_json(init_resp)
                        if init_data and "result" in init_data:
                            session_headers = dict(headers)
                            session_id = init_resp.headers.get("mcp-session-id")
                            if session_id:
                                session_headers["mcp-session-id"] = session_id

                            # Send notifications/initialized
                            notify_payload = {
                                "jsonrpc": "2.0",
                                "method": "notifications/initialized",
                            }
                            try:
                                await client.post(
                                    url,
                                    json=notify_payload,
                                    headers=session_headers,
                                )
                            except Exception:
                                pass

                            # Send tools/list
                            tools_list_payload = {
                                "jsonrpc": "2.0",
                                "id": 2,
                                "method": "tools/list",
                                "params": {},
                            }
                            list_resp = await client.post(
                                url,
                                json=tools_list_payload,
                                headers=session_headers,
                            )
                            if list_resp.status_code == 200:
                                list_data = _parse_response_json(list_resp)
                                tools = _extract_tools_from_response(list_data)
                                if tools is not None:
                                    logger.info(
                                        "Discovered %d tools from %s via MCP initialize handshake",
                                        len(tools),
                                        url,
                                    )
                                    return tools
                            else:
                                last_error = f"HTTP {list_resp.status_code} from {url} on tools/list"
                            break  # Successfully initialized with this protocol version
                    elif init_resp.status_code != 404:
                        last_error = f"HTTP {init_resp.status_code} from {url} on initialize"
                except Exception as e:
                    last_error = e

        # 2. Try REST GET endpoints fallback (/tools, /tools/list)
        get_candidate_urls = []
        if clean_base.endswith("/tools") or clean_base.endswith("/tools/list"):
            get_candidate_urls.append(clean_base)
        else:
            get_candidate_urls.extend([f"{clean_base}/tools", f"{clean_base}/tools/list"])
        get_candidate_urls = list(dict.fromkeys(get_candidate_urls))

        for url in get_candidate_urls:
            try:
                resp = await client.get(url, headers=headers)
                if resp.status_code == 200:
                    data = _parse_response_json(resp)
                    tools = _extract_tools_from_response(data)
                    if tools is not None:
                        logger.info("Discovered %d tools from %s via REST", len(tools), url)
                        return tools
                else:
                    last_error = f"HTTP {resp.status_code} from {url}"
            except Exception as e:
                last_error = e
                continue

        err_msg = (
            f"Failed to discover MCP tools from {backend_url}. "
            f"Neither JSON-RPC nor REST endpoints responded with valid tools. (Last error: {last_error})"
        )
        logger.error(err_msg)
        raise RuntimeError(err_msg)
    finally:
        if close_client:
            await client.aclose()


def _normalize_tools(raw_tools: List[Any]) -> List[Dict[str, Any]]:
    """Normalize and validate discovered tool schemas."""
    normalized: List[Dict[str, Any]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if not name:
            continue
        tool_entry: Dict[str, Any] = {"name": name}
        if "description" in item and item["description"] is not None:
            tool_entry["description"] = item["description"]
        if "inputSchema" in item and item["inputSchema"] is not None:
            tool_entry["inputSchema"] = item["inputSchema"]
        elif "parameters" in item and item["parameters"] is not None:
            tool_entry["inputSchema"] = item["parameters"]
        normalized.append(tool_entry)
    return normalized


def merge_tools_with_existing(
    existing_tools: List[Dict[str, Any]],
    discovered_tools: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """
    Merge newly discovered tools with existing tool definitions.
    Preserves user customizations such as 'hidden_parameters', 'hidden_fields',
    and 'defaults' from the existing configuration.
    """
    existing_by_name: Dict[str, Dict[str, Any]] = {}
    for t in existing_tools:
        if isinstance(t, dict) and t.get("name"):
            existing_by_name[t["name"]] = t

    merged: List[Dict[str, Any]] = []
    for tool in discovered_tools:
        tool_copy = copy.deepcopy(tool)
        name = tool_copy.get("name")
        if name and name in existing_by_name:
            orig = existing_by_name[name]
            for preserve_key in (
                "hidden_parameters",
                "hidden_fields",
                "defaults",
                "parameter_defaults",
            ):
                if preserve_key in orig:
                    tool_copy[preserve_key] = copy.deepcopy(orig[preserve_key])
        merged.append(tool_copy)

    return merged


def update_yaml_config_file(
    config_path: str | Path,
    server_name: str,
    new_tools: List[Dict[str, Any]],
    force: bool = False,
) -> bool:
    """
    Update the 'tools' section for a specific MCP server directly in the YAML configuration file.
    Only proceeds if auto_discover is True on that server, unless force=True.
    Preserves custom fields like hidden_parameters.
    """
    path = Path(config_path)
    if not path.is_file():
        logger.error("Configuration file not found at %s", config_path)
        return False

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    mcp_servers = data.get("mcp_servers")
    if not isinstance(mcp_servers, list):
        logger.error("No 'mcp_servers' list found in %s", config_path)
        return False

    server_entry = next((s for s in mcp_servers if isinstance(s, dict) and s.get("name") == server_name), None)
    if not server_entry:
        logger.error("Server '%s' not found in %s", server_name, config_path)
        return False

    is_auto = server_entry.get("auto_discover", False)
    if not is_auto and not force:
        logger.info(
            "Server '%s' has auto_discover=false; skipping YAML update to protect user edits.",
            server_name,
        )
        return False

    existing_tools = server_entry.get("tools") or []
    merged_tools = merge_tools_with_existing(existing_tools, new_tools)
    server_entry["tools"] = merged_tools

    with open(path, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)

    logger.info("Successfully updated tools for server '%s' directly in %s", server_name, config_path)
    return True


async def sync_mcp_server(
    config: AppConfig,
    server_name: str,
    config_path: Optional[str | Path] = None,
    wake_manager: Optional[WakeManager] = None,
    force: bool = False,
) -> List[Dict[str, Any]]:
    """
    Perform a complete discovery and synchronization cycle for an MCP server:
    1. Verify server is configured.
    2. Check auto_discover or force flag.
    3. Wake backend if unreachable (via wake_manager if provided).
    4. Fetch tools from upstream MCP server.
    5. Merge with existing customizations (hidden_parameters, etc.).
    6. Write updated tools directly to YAML file (if config_path provided).
    7. Update in-memory configuration.
    """
    server_cfg = next((s for s in config.mcp_servers if s.name == server_name), None)
    if not server_cfg:
        raise ValueError(f"MCP server '{server_name}' is not configured.")

    if not server_cfg.auto_discover and not force:
        raise ValueError(
            f"MCP server '{server_name}' has auto_discover=false. "
            "Enable 'auto_discover: true' or use force=True to update."
        )

    # Wake backend if wake_manager is provided
    if wake_manager is not None:
        auth_header = None
        if server_cfg.bearer_token:
            token = server_cfg.bearer_token.strip()
            auth_val = token if token.lower().startswith("bearer ") else f"Bearer {token}"
            auth_header = {"Authorization": auth_val}

        success, err_msg = await wake_manager.ensure_awake(
            server_cfg.backend_url,
            health_path=server_cfg.health_path,
            headers=auth_header,
        )
        if not success:
            raise RuntimeError(f"Could not reach MCP server '{server_name}': {err_msg}")

    # Fetch tools from upstream
    raw_discovered = await fetch_tools_from_mcp_server(
        backend_url=server_cfg.backend_url,
        bearer_token=server_cfg.bearer_token,
    )

    merged_tools = merge_tools_with_existing(server_cfg.tools, raw_discovered)

    # Save directly to config.yaml if path is available
    if config_path:
        update_yaml_config_file(config_path, server_name, merged_tools, force=force)

    # Update in-memory AppConfig object
    server_cfg.tools = merged_tools

    return merged_tools


async def _run_cli(args: argparse.Namespace) -> int:
    """CLI execution handler."""
    if args.url:
        print(f"Discovering tools from {args.url}...")
        try:
            tools = await fetch_tools_from_mcp_server(
                backend_url=args.url,
                bearer_token=args.token,
            )
            print(f"Successfully discovered {len(tools)} tools:")
            yaml_out = yaml.safe_dump(tools, sort_keys=False, default_flow_style=False)
            print(yaml_out)
            return 0
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    config_path = args.config
    cfg = load_config(config_path)
    actual_path = getattr(cfg, "config_path", config_path) or "config.yaml"

    servers_to_sync = []
    if args.all:
        servers_to_sync = [s.name for s in cfg.mcp_servers if s.auto_discover or args.force]
    elif args.server:
        servers_to_sync = [args.server]
    else:
        print("Error: Specify --server <name>, --all, or --url <endpoint>.", file=sys.stderr)
        return 1

    if not servers_to_sync:
        print("No servers found to sync (ensure servers have auto_discover: true or use --force).")
        return 0

    exit_code = 0
    for s_name in servers_to_sync:
        print(f"\n--- Syncing MCP Server: {s_name} ---")
        try:
            target_srv = next((s for s in cfg.mcp_servers if s.name == s_name), None)
            if not target_srv:
                print(f"Error: Server '{s_name}' not found in configuration.", file=sys.stderr)
                exit_code = 1
                continue

            if not target_srv.auto_discover and not args.force:
                print(f"Skipping '{s_name}' because auto_discover=false (use --force to override).")
                continue

            raw_tools = await fetch_tools_from_mcp_server(
                backend_url=target_srv.backend_url,
                bearer_token=target_srv.bearer_token,
            )
            merged = merge_tools_with_existing(target_srv.tools, raw_tools)
            print(f"Discovered {len(merged)} tools for '{s_name}'.")

            if args.dry_run:
                print("Dry-run mode: tools that would be written to config:")
                print(yaml.safe_dump(merged, sort_keys=False, default_flow_style=False))
            else:
                updated = update_yaml_config_file(actual_path, s_name, merged, force=args.force)
                if updated:
                    print(f"Updated '{s_name}' tools directly in {actual_path}!")
                else:
                    print(f"Failed to update YAML for '{s_name}'.")
                    exit_code = 1
        except Exception as e:
            print(f"Failed to sync '{s_name}': {e}", file=sys.stderr)
            exit_code = 1

    return exit_code


def main() -> None:
    """Main CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Llama Gateway MCP Tool Auto-Discovery Utility")
    parser.add_argument("--config", "-c", default=None, help="Path to config.yaml")
    parser.add_argument("--server", "-s", help="Name of MCP server configured in config.yaml to sync")
    parser.add_argument("--all", action="store_true", help="Sync all servers with auto_discover: true")
    parser.add_argument("--url", help="Direct MCP server URL to probe")
    parser.add_argument("--token", help="Bearer token for MCP server (used with --url)")
    parser.add_argument("--force", "-f", action="store_true", help="Force discovery and YAML update even if auto_discover=false")
    parser.add_argument("--dry-run", action="store_true", help="Print discovered YAML tools without updating config.yaml")

    args = parser.parse_args()
    ret = asyncio.run(_run_cli(args))
    sys.exit(ret)


if __name__ == "__main__":
    main()
