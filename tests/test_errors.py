"""Upstream failures, shared by both tools: every one becomes a structured error.

Each case runs against both tools. search_countries wraps the error in a one-item list
(the starter's contract); get_country_details returns the error dict itself.
"""
from __future__ import annotations

from typing import Any, Callable

import httpx
import pytest

import server
from conftest import FAKE_KEY, MockApi


def call_search() -> dict[str, Any]:
    (error,) = server.search_countries("germany")
    return error


def call_details() -> dict[str, Any]:
    return server.get_country_details("germany")


TOOLS = pytest.mark.parametrize("call", [call_search, call_details], ids=["search", "details"])


def raise_(exc: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise exc

    return handler


def error_body(message: str, code: str | None = None) -> dict[str, Any]:
    return {"errors": [{"message": message, **({"code": code} if code else {})}]}


@TOOLS
def test_missing_api_key(api: MockApi, monkeypatch: pytest.MonkeyPatch, call: Callable[[], dict]) -> None:
    monkeypatch.delenv(server.API_KEY_ENV)

    error = call()

    assert error["code"] == "missing_api_key"
    assert server.API_KEY_ENV in error["error"]
    assert api.requests == []


@TOOLS
def test_blank_api_key_counts_as_missing(api: MockApi, monkeypatch: pytest.MonkeyPatch, call: Callable[[], dict]) -> None:
    monkeypatch.setenv(server.API_KEY_ENV, "   ")

    assert call()["code"] == "missing_api_key"
    assert api.requests == []


@TOOLS
@pytest.mark.parametrize(
    "bad_key",
    ["\x16", "rc_\x07key", "rc_€_key", "rc key with spaces"],
    ids=["ctrl-v-paste", "bell", "non-ascii", "inner-spaces"],
)
def test_malformed_api_key_is_rejected_before_any_request(
    api: MockApi, monkeypatch: pytest.MonkeyPatch, call: Callable[[], dict], bad_key: str
) -> None:
    # Found in live testing: a Ctrl+V into a masked prompt stored "\x16" as the key.
    monkeypatch.setenv(server.API_KEY_ENV, bad_key)

    error = call()

    assert error["code"] == "invalid_api_key"
    assert bad_key not in error["error"]
    assert api.requests == []


@TOOLS
@pytest.mark.parametrize(
    ("status", "body", "code", "fragment"),
    [
        (401, error_body("No matching authorization key found.", "authKeyNotFound"), "auth_failed", "No matching authorization key found."),
        (403, error_body("Account frozen."), "forbidden", "Account frozen."),
        (429, None, "rate_limited", "20 requests per 10 seconds"),
        (500, None, "upstream_error", "HTTP 500"),
        (503, "<html>Service Unavailable</html>", "upstream_error", "HTTP 503"),
        (301, None, "upstream_error", "may have moved"),
        (400, error_body("Limit must be an integer between 1 and 100."), "upstream_error", "Limit must be an integer"),
    ],
)
def test_http_errors(
    api: MockApi, call: Callable[[], dict], status: int, body: Any, code: str, fragment: str
) -> None:
    if isinstance(body, str):
        api.respond(lambda r: httpx.Response(status, text=body))
    else:
        api.respond(lambda r: httpx.Response(status, json=body, headers={"Location": "https://example.invalid/"}))

    error = call()

    assert error["code"] == code
    assert error["status"] == status
    assert fragment in error["error"]


@TOOLS
@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (httpx.ReadTimeout("read timed out"), "timeout"),
        (httpx.ConnectTimeout("connect timed out"), "timeout"),
        (httpx.ConnectError("name resolution failed"), "network_error"),
        (httpx.RemoteProtocolError("peer closed connection"), "network_error"),
    ],
)
def test_transport_failures(api: MockApi, call: Callable[[], dict], exc: Exception, code: str) -> None:
    api.respond(raise_(exc))

    assert call()["code"] == code


@TOOLS
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="<html>not json</html>"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"data": None}),
        httpx.Response(200, json={"data": {"objects": "nope"}}),
        httpx.Response(200, json={"success": False, "data": None, "errors": [{"message": "deprecated"}]}),
    ],
    ids=["html", "json-list", "null-data", "objects-not-list", "legacy-deprecation-envelope"],
)
def test_malformed_responses(api: MockApi, call: Callable[[], dict], response: httpx.Response) -> None:
    api.respond(lambda r: response)

    assert call()["code"] == "bad_response"


@TOOLS
def test_details_stops_at_first_failure(api: MockApi, call: Callable[[], dict]) -> None:
    api.respond(raise_(httpx.ReadTimeout("slow")))

    call()

    assert len(api.requests) == 1  # no fallback lookups after an upstream failure


def test_errors_never_leak_the_api_key(api: MockApi) -> None:
    # Echo the key back in an error body and an exception message; neither may reach the model.
    leaky_cases: list[Callable[[httpx.Request], httpx.Response]] = [
        lambda r: httpx.Response(401, json=error_body(f"bad key {FAKE_KEY}")),
        raise_(httpx.ConnectError(f"failed with Authorization: Bearer {FAKE_KEY}")),
        lambda r: httpx.Response(500, text=FAKE_KEY),
    ]
    for handler in leaky_cases:
        api.respond(handler)
        for call in (call_search, call_details):
            error = call()
            assert "error" in error
            assert FAKE_KEY not in repr(error)


def test_upstream_message_is_shown_with_key_redacted(api: MockApi) -> None:
    api.respond(lambda r: httpx.Response(401, json=error_body(f"bad key {FAKE_KEY}")))

    assert "bad key [REDACTED]" in call_details()["error"]
