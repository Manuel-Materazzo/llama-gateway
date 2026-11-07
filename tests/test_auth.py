"""Unit tests for gateway/auth.py authentication models and middleware."""

from starlette.requests import Request
from starlette.datastructures import Headers, QueryParams
import pytest
from fastapi import HTTPException

from gateway.auth import AuthConfig, verify_upstream_auth


def _make_dummy_request(headers=None, query_params=None) -> Request:
    raw_headers = []
    if headers:
        for k, v in headers.items():
            raw_headers.append((k.lower().encode("latin-1"), v.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "headers": raw_headers,
        "query_string": "&".join(f"{k}={v}" for k, v in (query_params or {}).items()).encode("ascii"),
    }
    return Request(scope)


def test_auth_config_type_detection():
    # String as bearer
    cfg1 = AuthConfig.model_validate("my-token")
    assert cfg1.type == "bearer"
    assert cfg1.token == "my-token"

    cfg1b = AuthConfig.model_validate("Bearer prefixed-token")
    assert cfg1b.type == "bearer"
    assert cfg1b.token == "prefixed-token"

    # Auto-detect basic from username/password
    cfg2 = AuthConfig.model_validate({"username": "alice", "password": "secret"})
    assert cfg2.type == "basic"
    assert cfg2.username == "alice"
    assert cfg2.password == "secret"

    # Auto-detect header from header_name / value
    cfg3 = AuthConfig.model_validate({"header": "X-API-Key", "value": "12345"})
    assert cfg3.type == "header"
    assert cfg3.header_name == "X-API-Key"
    assert cfg3.header_value == "12345"

    # Auto-detect headers dictionary
    cfg4 = AuthConfig.model_validate({"headers": {"X-Custom": "val1", "X-Other": "val2"}})
    assert cfg4.type == "headers"
    assert cfg4.headers["X-Custom"] == "val1"

    # None / empty
    cfg5 = AuthConfig.model_validate({})
    assert cfg5.type == "none"


def test_auth_config_get_headers():
    # Bearer
    cfg_bearer = AuthConfig(type="bearer", token="token123")
    assert cfg_bearer.get_headers() == {"Authorization": "Bearer token123"}

    # Basic
    cfg_basic = AuthConfig(type="basic", username="user", password="pwd")
    import base64
    expected_b64 = base64.b64encode(b"user:pwd").decode("ascii")
    assert cfg_basic.get_headers() == {"Authorization": f"Basic {expected_b64}"}

    # Header
    cfg_header = AuthConfig(type="header", header_name="X-Auth-Token", header_value="xyz")
    assert cfg_header.get_headers() == {"X-Auth-Token": "xyz"}

    # Headers
    cfg_headers = AuthConfig(type="headers", headers={"A": "1", "B": "2"})
    assert cfg_headers.get_headers() == {"A": "1", "B": "2"}

    # None
    cfg_none = AuthConfig(type="none")
    assert cfg_none.get_headers() == {}


def test_auth_authenticate_request_bearer():
    cfg = AuthConfig(type="bearer", token="my-secret")

    # Correct header
    req = _make_dummy_request(headers={"Authorization": "Bearer my-secret"})
    ok, err = cfg.authenticate_request(req)
    assert ok is True
    assert err is None

    # Correct header without "Bearer " prefix in header
    req = _make_dummy_request(headers={"Authorization": "my-secret"})
    ok, err = cfg.authenticate_request(req)
    assert ok is True

    # Invalid header
    req = _make_dummy_request(headers={"Authorization": "Bearer wrong"})
    ok, err = cfg.authenticate_request(req)
    assert ok is False
    assert "Invalid Bearer token" in err

    # Query param fallback (?token=my-secret)
    req = _make_dummy_request(query_params={"token": "my-secret"})
    ok, err = cfg.authenticate_request(req)
    assert ok is True

    # Missing
    req = _make_dummy_request()
    ok, err = cfg.authenticate_request(req)
    assert ok is False
    assert "Missing" in err


def test_auth_authenticate_request_basic():
    import base64
    cfg = AuthConfig(type="basic", username="admin", password="password")

    # Missing header
    req = _make_dummy_request()
    ok, err = cfg.authenticate_request(req)
    assert ok is False
    assert "Missing Basic auth" in err

    # Wrong scheme
    req = _make_dummy_request(headers={"Authorization": "Bearer some-token"})
    ok, err = cfg.authenticate_request(req)
    assert ok is False
    assert "must use Basic scheme" in err

    # Malformed base64
    req = _make_dummy_request(headers={"Authorization": "Basic !!!not-base64!!!"})
    ok, err = cfg.authenticate_request(req)
    assert ok is False
    assert "Invalid base64" in err

    # Malformed decoded string (no colon)
    b64_no_colon = base64.b64encode(b"adminpassword").decode("ascii")
    req = _make_dummy_request(headers={"Authorization": f"Basic {b64_no_colon}"})
    ok, err = cfg.authenticate_request(req)
    assert ok is False
    assert "Malformed Basic auth credentials" in err

    # Valid credentials
    valid_b64 = base64.b64encode(b"admin:password").decode("ascii")
    req = _make_dummy_request(headers={"Authorization": f"Basic {valid_b64}"})
    ok, err = cfg.authenticate_request(req)
    assert ok is True
    assert err is None

    # Invalid credentials
    bad_b64 = base64.b64encode(b"admin:wrongpassword").decode("ascii")
    req = _make_dummy_request(headers={"Authorization": f"Basic {bad_b64}"})
    ok, err = cfg.authenticate_request(req)
    assert ok is False
    assert "Invalid Basic auth credentials" in err


def test_auth_authenticate_request_header_and_headers():
    cfg_hdr = AuthConfig(type="header", header_name="X-Special-Key", header_value="key123")
    
    # Valid header (case insensitive header lookup)
    req = _make_dummy_request(headers={"x-special-key": "key123"})
    ok, err = cfg_hdr.authenticate_request(req)
    assert ok is True

    # Invalid value
    req = _make_dummy_request(headers={"x-special-key": "wrong"})
    ok, err = cfg_hdr.authenticate_request(req)
    assert ok is False

    # Missing header, fallback query param
    req = _make_dummy_request(query_params={"api_key": "key123"})
    ok, err = cfg_hdr.authenticate_request(req)
    assert ok is True

    # Multi-headers
    cfg_hdrs = AuthConfig(type="headers", headers={"X-Header-1": "val1", "X-Header-2": "val2"})
    req_good = _make_dummy_request(headers={"x-header-1": "val1", "x-header-2": "val2"})
    assert cfg_hdrs.authenticate_request(req_good)[0] is True

    req_partial = _make_dummy_request(headers={"x-header-1": "val1"})
    assert cfg_hdrs.authenticate_request(req_partial)[0] is False


def test_verify_upstream_auth_helper():
    cfg = AuthConfig(type="bearer", token="valid-token")

    # Valid -> passes without exception
    req_ok = _make_dummy_request(headers={"Authorization": "Bearer valid-token"})
    verify_upstream_auth(cfg, req_ok)

    # Invalid -> raises HTTPException 401
    req_bad = _make_dummy_request(headers={"Authorization": "Bearer bad-token"})
    with pytest.raises(HTTPException) as exc_info:
        verify_upstream_auth(cfg, req_bad)
    assert exc_info.value.status_code == 401
    assert "WWW-Authenticate" in exc_info.value.headers
