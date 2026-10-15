"""Shared fixtures: a fake API key and a mock REST Countries v5 transport.

Country objects below follow the v5 shape documented at
https://restcountries.com/docs/countries (`names`, `codes`, `capitals`, `currencies`,
`languages`, `borders`, `flag`), trimmed to the fields the server requests. Values are
illustrative test data, not live API output.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server  # noqa: E402

FAKE_KEY = "rc_test_key_not_a_real_secret"

GERMANY: dict[str, Any] = {
    "names": {"common": "Germany", "official": "Federal Republic of Germany"},
    "codes": {"alpha_2": "DE", "alpha_3": "DEU"},
    "capitals": [{"name": "Berlin", "attributes": {"primary": True}}],
    "region": "Europe",
    "subregion": "Western Europe",
    "population": 83491249,
    "area": {"kilometers": 357114, "miles": 137882.0},
    "currencies": [{"code": "EUR", "name": "Euro", "symbol": "€"}],
    "languages": [{"iso639_1": "de", "iso639_3": "deu", "name": "German", "native_name": "Deutsch"}],
    "borders": ["AUT", "BEL", "CZE", "DNK", "FRA", "LUX", "NLD", "POL", "CHE"],
    "flag": {
        "emoji": "🇩🇪",
        "url_png": "https://flags.restcountries.com/v5/w640/de.png",
        "url_svg": "https://flags.restcountries.com/v5/svg/de.svg",
    },
}

SOUTH_AFRICA: dict[str, Any] = {
    "names": {"common": "South Africa", "official": "Republic of South Africa"},
    "codes": {"alpha_2": "ZA", "alpha_3": "ZAF"},
    # Three capitals; the server must report the one flagged primary, not just the first.
    "capitals": [
        {"name": "Cape Town", "attributes": {"primary": False, "legislative": True}},
        {"name": "Pretoria", "attributes": {"primary": True, "executive": True}},
        {"name": "Bloemfontein", "attributes": {"primary": False, "judicial": True}},
    ],
    "region": "Africa",
    "population": 63100945,
}


def country(common: str, **extra: Any) -> dict[str, Any]:
    """A minimal v5 country object."""
    return {"names": {"common": common, "official": extra.pop("official", common)}, **extra}


def envelope(*objects: dict[str, Any]) -> dict[str, Any]:
    return {"data": {"objects": list(objects), "meta": {"total": len(objects), "count": len(objects)}}}


class MockApi:
    """Records requests and answers each with `handler(request)`."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.handler: Callable[[httpx.Request], httpx.Response] = lambda r: httpx.Response(404)

    def respond(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    def respond_json(self, payload: Any, status: int = 200) -> None:
        self.handler = lambda r: httpx.Response(status, json=payload)

    def respond_by_path(self, routes: dict[str, httpx.Response]) -> None:
        """Answer by URL path suffix; unknown paths get 404 (no match)."""

        def handler(request: httpx.Request) -> httpx.Response:
            for suffix, response in routes.items():
                if request.url.raw_path.decode().split("?")[0].endswith(suffix):
                    return response
            return httpx.Response(404, json={"errors": [{"message": "Not found"}]})

        self.handler = handler

    def _transport(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)

    def client(self) -> httpx.Client:
        return httpx.Client(
            base_url=server.BASE_URL, transport=httpx.MockTransport(self._transport), follow_redirects=False
        )


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> MockApi:
    """A configured API key plus a mocked REST Countries transport."""
    monkeypatch.setenv(server.API_KEY_ENV, FAKE_KEY)
    mock = MockApi()
    monkeypatch.setattr(server, "_make_client", mock.client)
    return mock


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"
