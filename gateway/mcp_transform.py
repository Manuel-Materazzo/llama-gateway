"""MCP tool transformation utilities for hiding schema fields and injecting default parameters."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("llama_gateway.mcp_transform")


def extract_tool_hidden_info(tool_dict: Dict[str, Any]) -> Tuple[Set[str], Dict[str, Any]]:
    """
    Extract hidden field names and default values from a tool configuration dictionary.
    Supports 'hidden_fields', 'hidden_parameters', 'defaults', and 'parameter_defaults'.
    """
    hidden_keys: Set[str] = set()
    defaults: Dict[str, Any] = {}

    # Extract explicit hidden_fields list
    if "hidden_fields" in tool_dict and isinstance(tool_dict["hidden_fields"], (list, set, tuple)):
        hidden_keys.update(tool_dict["hidden_fields"])

    # Extract from hidden_parameters, defaults, or parameter_defaults
    for key in ("hidden_parameters", "defaults", "parameter_defaults"):
        if key in tool_dict and isinstance(tool_dict[key], dict):
            defaults.update(tool_dict[key])
            hidden_keys.update(tool_dict[key].keys())

    return hidden_keys, defaults


def sanitize_tool_definition(tool: Dict[str, Any]) -> Dict[str, Any]:
    """
    Create a clean copy of an MCP tool definition by removing hidden fields from its inputSchema
    and stripping internal gateway configuration keys.
    """
    sanitized = copy.deepcopy(tool)
    hidden_keys, _ = extract_tool_hidden_info(tool)

    # Clean inputSchema if present
    schema = sanitized.get("inputSchema")
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for k in hidden_keys:
                properties.pop(k, None)

        required = schema.get("required")
        if isinstance(required, list):
            schema["required"] = [r for r in required if r not in hidden_keys]

    # Remove internal metadata keys from the exposed tool dictionary
    for meta_key in ("hidden_fields", "hidden_parameters", "defaults", "parameter_defaults"):
        sanitized.pop(meta_key, None)

    return sanitized


def get_sanitized_server_tools(server_config_dict: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Get all sanitized tools for a given MCP server configuration.
    Accepts tools defined under 'tools' or 'static_tools'.
    """
    raw_tools = server_config_dict.get("tools") or server_config_dict.get("static_tools") or []
    return [sanitize_tool_definition(t) for t in raw_tools if isinstance(t, dict)]


def get_tool_defaults_map(server_config_dict: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    """
    Build a map of tool_name -> default_parameters for an MCP server.
    """
    raw_tools = server_config_dict.get("tools") or server_config_dict.get("static_tools") or []
    defaults_map: Dict[str, Dict[str, Any]] = {}

    for tool in raw_tools:
        if isinstance(tool, dict):
            name = tool.get("name")
            if name:
                _, defaults = extract_tool_hidden_info(tool)
                if defaults:
                    defaults_map[name] = defaults

    return defaults_map


def transform_mcp_payload(
    server_config_dict: Dict[str, Any],
    body_bytes: bytes,
    path_tool_name: Optional[str] = None,
) -> bytes:
    """
    Inspect an incoming MCP request payload and inject hidden default parameters
    for any matching tool before forwarding to the upstream MCP backend.
    """
    if not body_bytes:
        return body_bytes

    try:
        data = json.loads(body_bytes.decode("utf-8"))
    except Exception:
        # If not JSON, return unmodified
        return body_bytes

    defaults_map = get_tool_defaults_map(server_config_dict)
    if not defaults_map:
        return body_bytes

    modified = False

    if isinstance(data, dict):
        # Case 1: JSON-RPC 2.0 tool call (e.g. {"method": "tools/call", "params": {"name": "...", "arguments": {...}}})
        if data.get("method") in ("tools/call", "tool/call", "call_tool") and isinstance(data.get("params"), dict):
            params = data["params"]
            tool_name = params.get("name") or path_tool_name
            if tool_name and tool_name in defaults_map:
                defaults = defaults_map[tool_name]
                current_args = params.get("arguments")
                if current_args is None:
                    params["arguments"] = copy.deepcopy(defaults)
                    modified = True
                elif isinstance(current_args, dict):
                    # Combine defaults with provided arguments (provided args take precedence if duplicate)
                    merged = copy.deepcopy(defaults)
                    merged.update(current_args)
                    params["arguments"] = merged
                    modified = True

        # Case 2: REST tool call (e.g. {"name": "...", "arguments": {...}} or {"name": "...", "parameters": {...}})
        tool_name = data.get("name") or path_tool_name
        if tool_name and tool_name in defaults_map:
            defaults = defaults_map[tool_name]
            for arg_field in ("arguments", "parameters", "args"):
                if arg_field in data:
                    current_args = data[arg_field]
                    if current_args is None:
                        data[arg_field] = copy.deepcopy(defaults)
                        modified = True
                    elif isinstance(current_args, dict):
                        merged = copy.deepcopy(defaults)
                        merged.update(current_args)
                        data[arg_field] = merged
                        modified = True

            # If body directly contains the parameters without an "arguments" wrapper and tool name is known
            if not any(k in data for k in ("arguments", "parameters", "args", "jsonrpc", "method")):
                for k, v in defaults.items():
                    if k not in data:
                        data[k] = v
                        modified = True

    if modified:
        logger.info(
            "Injected hidden MCP default parameters into request for server '%s'",
            server_config_dict.get("name", "unknown"),
        )
        return json.dumps(data).encode("utf-8")

    return body_bytes
