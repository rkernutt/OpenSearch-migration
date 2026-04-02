"""
Tests for utilities added in the April 2026 hardening pass:
  - validate_index_name()
  - _redact_response_text()
  - DestAuth.apply() with api_key_encoded flag
  - elastic_headers_auth() from poll_reindex_task with encoded flag
  - validate_pair() early exit on invalid index names
"""

import base64

from validate_migration import (
    DestAuth,
    _redact_response_text,
    validate_index_name,
)

# ---------------------------------------------------------------------------
# validate_index_name
# ---------------------------------------------------------------------------


def test_valid_names() -> None:
    for name in ("my-index", "logs-2024.01.01", "metrics_v2", "index123"):
        assert validate_index_name(name) is None, f"Expected valid: {name!r}"


def test_uppercase_rejected() -> None:
    err = validate_index_name("MyIndex")
    assert err is not None
    assert "lowercase" in err


def test_leading_dash_rejected() -> None:
    err = validate_index_name("-bad-index")
    assert err is not None
    assert "'-'" in err or "start" in err


def test_leading_underscore_rejected() -> None:
    err = validate_index_name("_bad")
    assert err is not None


def test_leading_plus_rejected() -> None:
    err = validate_index_name("+bad")
    assert err is not None


def test_space_rejected() -> None:
    err = validate_index_name("my index")
    assert err is not None
    assert "invalid characters" in err


def test_comma_rejected() -> None:
    err = validate_index_name("idx,other")
    assert err is not None


def test_empty_rejected() -> None:
    err = validate_index_name("")
    assert err is not None
    assert "empty" in err


# ---------------------------------------------------------------------------
# _redact_response_text
# ---------------------------------------------------------------------------


def test_redact_apikey_header() -> None:
    text = 'error: {"Authorization": "ApiKey abc123XYZlongtoken=="}'
    result = _redact_response_text(text)
    assert "abc123XYZ" not in result
    assert "***" in result


def test_redact_bearer_token() -> None:
    text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9longtoken"
    result = _redact_response_text(text)
    assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9longtoken" not in result


def test_redact_leaves_short_strings() -> None:
    text = "Error: index not found"
    assert _redact_response_text(text) == text


def test_redact_long_base64() -> None:
    long_b64 = base64.b64encode(b"somesecretkeyvalue12345678901234567890").decode()
    text = f"key={long_b64}"
    result = _redact_response_text(text)
    assert long_b64 not in result
    assert "***" in result


# ---------------------------------------------------------------------------
# DestAuth — api_key_encoded flag
# ---------------------------------------------------------------------------


def test_dest_auth_raw_key_encoded() -> None:
    """Raw id:secret format should be Base64-encoded automatically."""
    raw = "myid:mysecret"
    auth = DestAuth(api_key=raw)
    headers, _ = auth.apply()
    expected = base64.b64encode(raw.encode()).decode()
    assert headers["Authorization"] == f"ApiKey {expected}"


def test_dest_auth_already_encoded_not_re_encoded() -> None:
    """Pre-encoded key must not be double-encoded."""
    encoded = base64.b64encode(b"myid:mysecret").decode()
    auth = DestAuth(api_key=encoded, api_key_encoded=True)
    headers, _ = auth.apply()
    assert headers["Authorization"] == f"ApiKey {encoded}"


def test_dest_auth_no_colon_not_encoded() -> None:
    """A key with no colon is assumed already encoded; no transformation."""
    key = "alreadyencodedkeywithoutseparator"
    auth = DestAuth(api_key=key)
    headers, _ = auth.apply()
    assert headers["Authorization"] == f"ApiKey {key}"


def test_dest_auth_basic_fallback() -> None:
    auth = DestAuth(user="bob", password="s3cr3t")
    headers, a = auth.apply()
    assert "Authorization" not in headers
    assert a == ("bob", "s3cr3t")


def test_dest_auth_no_creds_returns_empty() -> None:
    auth = DestAuth()
    headers, a = auth.apply()
    assert "Authorization" not in headers
    assert a is None


# ---------------------------------------------------------------------------
# elastic_headers_auth from poll_reindex_task
# ---------------------------------------------------------------------------


def test_poll_elastic_headers_raw_key() -> None:
    from poll_reindex_task import elastic_headers_auth

    raw = "id123:secretabc"
    h, a = elastic_headers_auth(raw, None, None, api_key_encoded=False)
    expected = base64.b64encode(raw.encode()).decode()
    assert h["Authorization"] == f"ApiKey {expected}"
    assert a is None


def test_poll_elastic_headers_encoded_key() -> None:
    from poll_reindex_task import elastic_headers_auth

    encoded = base64.b64encode(b"id123:secretabc").decode()
    h, a = elastic_headers_auth(encoded, None, None, api_key_encoded=True)
    assert h["Authorization"] == f"ApiKey {encoded}"


def test_poll_elastic_headers_basic() -> None:
    from poll_reindex_task import elastic_headers_auth

    h, a = elastic_headers_auth(None, "user", "pass")
    assert "Authorization" not in h
    assert a == ("user", "pass")


# ---------------------------------------------------------------------------
# validate_pair — early exit on invalid index name
# ---------------------------------------------------------------------------


def test_validate_pair_rejects_uppercase_source() -> None:
    from validate_migration import DestAuth, validate_pair

    ok, detail, category = validate_pair(
        source_host="https://example.com",
        dest_host="https://dest.com",
        source_index="MyIndex",
        dest_index="myindex",
        use_sigv4=False,
        source_region="us-east-1",
        source_user="u",
        source_password="p",
        dest_auth=DestAuth(user="u", password="p"),
        check_existence=False,
        sample_size=0,
    )
    assert not ok
    assert category == "validation"
    assert "source" in detail.lower()


def test_validate_pair_rejects_invalid_dest() -> None:
    from validate_migration import DestAuth, validate_pair

    ok, detail, category = validate_pair(
        source_host="https://example.com",
        dest_host="https://dest.com",
        source_index="myindex",
        dest_index="My Dest Index",
        use_sigv4=False,
        source_region="us-east-1",
        source_user="u",
        source_password="p",
        dest_auth=DestAuth(user="u", password="p"),
        check_existence=False,
        sample_size=0,
    )
    assert not ok
    assert category == "validation"
    assert "destination" in detail.lower()
