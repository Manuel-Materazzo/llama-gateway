"""Authentication models, helpers, and verification for Llama Gateway.

Supports flexible downstream authentication (forwarded to backends like MCP servers)
and upstream authentication (required from clients connecting to the Gateway), acting
as an authentication middleware.
"""

from __future__ import annotations

import base64
import logging
import secrets
from typing import Any, Dict, List, Optional, Tuple
from fastapi import HTTPException, Request
from pydantic import BaseModel, Field, model_validator

logger = logging.getLogger("llama_gateway.auth")


class AuthConfig(BaseModel):
    """
    Configuration for authentication (used for both downstream and upstream).

    Supported types:
    - "bearer": Bearer token auth (e.g. Authorization: Bearer <token>)
    - "basic": HTTP Basic auth (e.g. Authorization: Basic <base64>)
    - "header" / "api_key": Custom header or API key (e.g. X-API-Key: <key>)
    - "headers": Arbitrary multiple headers dictionary
    - "none": No authentication
    """

    type: str = "none"
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None
    header_name: Optional[str] = None
    header_value: Optional[str] = None
    headers: Dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def remap_auth_fields(cls, data: Any) -> Any:
        if isinstance(data, str):
            val = data.strip()
            if val.lower().startswith("bearer "):
                return {"type": "bearer", "token": val[7:].strip()}
            return {"type": "bearer", "token": val}

        if isinstance(data, dict):
            d = dict(data)

            # Field aliases
            if "header" in d and "header_name" not in d:
                d["header_name"] = d["header"]
            elif "name" in d and "header_name" not in d:
                d["header_name"] = d["name"]

            if "value" in d and "header_value" not in d:
                d["header_value"] = d["value"]
            elif "key" in d and "header_value" not in d:
                d["header_value"] = d["key"]
            elif "api_key" in d and "header_value" not in d:
                d["header_value"] = d["api_key"]

            if "auth_token" in d and "token" not in d:
                d["token"] = d["auth_token"]
            elif "bearer_token" in d and "token" not in d:
                d["token"] = d["bearer_token"]

            if "user" in d and "username" not in d:
                d["username"] = d["user"]
            if "pass" in d and "password" not in d:
                d["password"] = d["pass"]
            elif "pwd" in d and "password" not in d:
                d["password"] = d["pwd"]

            # Type normalization and auto-detection
            raw_type = d.get("type")
            if raw_type:
                t = str(raw_type).strip().lower()
                if t in ("api_key", "custom_header"):
                    t = "header"
                d["type"] = t
            else:
                if d.get("username") is not None or d.get("password") is not None:
                    d["type"] = "basic"
                elif d.get("headers"):
                    d["type"] = "headers"
                elif d.get("header_name") or d.get("header_value"):
                    d["type"] = "header"
                elif d.get("token"):
                    d["type"] = "bearer"
                else:
                    d["type"] = "none"

            return d

        return data

    def get_headers(self) -> Dict[str, str]:
        """Generate headers to send when connecting downstream."""
        auth_type = (self.type or "none").lower()

        if auth_type == "bearer":
            t = (self.token or "").strip()
            auth_val = t if t.lower().startswith("bearer ") else f"Bearer {t}"
            return {"Authorization": auth_val}

        if auth_type == "basic":
            u = self.username or ""
            p = self.password or ""
            raw = f"{u}:{p}".encode("utf-8")
            encoded = base64.b64encode(raw).decode("ascii")
            return {"Authorization": f"Basic {encoded}"}

        if auth_type == "header":
            h_name = self.header_name or "X-API-Key"
            h_val = self.header_value if self.header_value is not None else (self.token or "")
            return {h_name: h_val}

        if auth_type == "headers":
            return dict(self.headers)

        return {}

    def get_consumed_header_names(self) -> List[str]:
        """
        Return lowercase header names consumed by this auth method so that
        the gateway can avoid leaking upstream client credentials downstream.
        """
        auth_type = (self.type or "none").lower()
        if auth_type in ("bearer", "basic"):
            return ["authorization"]
        if auth_type == "header":
            return [(self.header_name or "x-api-key").lower()]
        if auth_type == "headers":
            return [k.lower() for k in self.headers.keys()]
        return []

    def authenticate_request(self, request: Request) -> Tuple[bool, Optional[str]]:
        """
        Validate incoming request from client against this auth specification.
        Returns (is_authenticated, optional_error_message).
        """
        auth_type = (self.type or "none").lower()
        if auth_type == "none":
            return True, None

        if auth_type == "bearer":
            expected_token = (self.token or "").strip()
            # 1. Check Authorization header
            auth_header = request.headers.get("authorization", "").strip()
            if auth_header:
                if auth_header.lower().startswith("bearer "):
                    provided_token = auth_header[7:].strip()
                else:
                    provided_token = auth_header

                if secrets.compare_digest(provided_token, expected_token):
                    return True, None
                return False, "Invalid Bearer token"

            # 2. Check query parameter fallback (?token=... or ?api_key=...)
            query_token = request.query_params.get("token") or request.query_params.get("api_key")
            if query_token and secrets.compare_digest(query_token.strip(), expected_token):
                return True, None

            return False, "Missing Bearer token in Authorization header"

        if auth_type == "basic":
            auth_header = request.headers.get("authorization", "").strip()
            if not auth_header:
                return False, "Missing Basic auth Authorization header"

            if not auth_header.lower().startswith("basic "):
                return False, "Authorization header must use Basic scheme"

            b64_payload = auth_header[6:].strip()
            try:
                decoded = base64.b64decode(b64_payload).decode("utf-8")
                if ":" not in decoded:
                    return False, "Malformed Basic auth credentials"
                user, pwd = decoded.split(":", 1)
            except Exception:
                return False, "Invalid base64 encoding in Basic auth header"

            exp_user = self.username or ""
            exp_pwd = self.password or ""
            user_ok = secrets.compare_digest(user, exp_user)
            pwd_ok = secrets.compare_digest(pwd, exp_pwd)

            if user_ok and pwd_ok:
                return True, None
            return False, "Invalid Basic auth credentials"

        if auth_type == "header":
            h_name = (self.header_name or "X-API-Key").lower()
            expected_val = self.header_value if self.header_value is not None else (self.token or "")

            # Look up case-insensitively in request headers
            for k, v in request.headers.items():
                if k.lower() == h_name:
                    if secrets.compare_digest(v, expected_val):
                        return True, None
                    return False, f"Invalid value for header '{self.header_name or h_name}'"

            # Query param fallback
            query_val = request.query_params.get(h_name) or request.query_params.get("api_key")
            if query_val and secrets.compare_digest(query_val, expected_val):
                return True, None

            return False, f"Missing required header '{self.header_name or h_name}'"

        if auth_type == "headers":
            for h_k, h_v in self.headers.items():
                matched = False
                for req_k, req_v in request.headers.items():
                    if req_k.lower() == h_k.lower():
                        if secrets.compare_digest(req_v, h_v):
                            matched = True
                        break
                if not matched:
                    return False, f"Missing or invalid required header '{h_k}'"
            return True, None

        return False, f"Unsupported authentication type '{self.type}'"

    def get_challenge_headers(self) -> Dict[str, str]:
        """Headers returned on HTTP 401 response."""
        auth_type = (self.type or "none").lower()
        if auth_type == "basic":
            return {"WWW-Authenticate": 'Basic realm="Llama Gateway"'}
        if auth_type == "header":
            h_name = self.header_name or "X-API-Key"
            return {"WWW-Authenticate": f'ApiKey realm="Llama Gateway", header="{h_name}"'}
        return {"WWW-Authenticate": 'Bearer realm="Llama Gateway", error="invalid_token"'}


def verify_upstream_auth(auth_config: Optional[AuthConfig], request: Request) -> None:
    """
    Verify incoming request against upstream authentication requirement.
    Raises HTTPException(401) if authentication fails.
    """
    if not auth_config or (auth_config.type or "none").lower() == "none":
        return

    is_authed, error_msg = auth_config.authenticate_request(request)
    if not is_authed:
        challenge_headers = auth_config.get_challenge_headers()
        raise HTTPException(
            status_code=401,
            detail=error_msg or "Unauthorized",
            headers=challenge_headers,
        )
