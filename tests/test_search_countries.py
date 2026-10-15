"""search_countries: success paths, input validation, and no-result cases."""
from __future__ import annotations

import httpx
import pytest

import server
from conftest import FAKE_KEY, GERMANY, SOUTH_AFRICA, MockApi, country, envelope


def test_returns_starter_shape(api: MockApi) -> None:
    api.respond_json(envelope(GERMANY))

    assert server.search_countries("germany") == [
        {"name": "Germany", "capital": "Berlin", "region": "Europe", "population": 83491249}
    ]


def test_sends_query_limit_fields_and_bearer_key(api: MockApi) -> None:
    api.respond_json(envelope(GERMANY))

    server.search_countries("  united   kingdom ", limit=3)

    (request,) = api.requests
    assert request.url.path == "/countries/v5/name"
    assert request.url.params["q"] == "united kingdom"  # trimmed and collapsed
    assert request.url.params["limit"] == "3"
    assert request.url.params["response_fields"] == server.SEARCH_FIELDS
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert FAKE_KEY not in str(request.url)  # key travels only in the header


def test_reports_the_primary_capital(api: MockApi) -> None:
    api.respond_json(envelope(SOUTH_AFRICA))

    assert server.search_countries("south africa")[0]["capital"] == "Pretoria"


def test_truncates_to_limit_even_if_api_returns_more(api: MockApi) -> None:
    api.respond_json(envelope(*(country(f"Country {i}") for i in range(10))))

    assert len(server.search_countries("country", limit=2)) == 2


def test_missing_fields_become_none(api: MockApi) -> None:
    api.respond_json(envelope({"names": {"common": "Antarctica"}}))

    assert server.search_countries("antarctica") == [
        {"name": "Antarctica", "capital": None, "region": None, "population": None}
    ]


@pytest.mark.parametrize(
    "response",
    [httpx.Response(404, json={"errors": [{"message": "Not found"}]}), httpx.Response(200, json=envelope())],
    ids=["404", "200-empty"],
)
def test_no_match_is_an_empty_list(api: MockApi, response: httpx.Response) -> None:
    api.respond(lambda r: response)

    assert server.search_countries("atlantis") == []


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("", "must not be empty"),
        ("   \t ", "must not be empty"),
        (None, "must be a string"),
        (42, "must be a string"),
        ("x" * 101, "at most 100"),
        ("../../admin", "may contain only"),
        ("germany?limit=100", "may contain only"),
        ("<script>", "may contain only"),
        ("123", "may contain only"),
        ("---", "may contain only"),
    ],
)
def test_invalid_query_never_calls_the_api(api: MockApi, query: object, message: str) -> None:
    result = server.search_countries(query)  # type: ignore[arg-type]

    assert len(result) == 1
    assert result[0]["code"] == "invalid_input"
    assert message in result[0]["error"]
    assert api.requests == []


@pytest.mark.parametrize("limit", [0, -1, 26, True, "5", 2.5, None])
def test_invalid_limit(api: MockApi, limit: object) -> None:
    result = server.search_countries("germany", limit=limit)  # type: ignore[arg-type]

    assert result[0]["code"] == "invalid_input"
    assert "limit" in result[0]["error"]
    assert api.requests == []


@pytest.mark.parametrize("query", ["Côte d'Ivoire", "Guinea-Bissau", "St. Lucia", "Congo (Kinshasa)", "Åland"])
def test_accepts_real_country_name_punctuation(api: MockApi, query: str) -> None:
    api.respond_json(envelope())

    assert server.search_countries(query) == []
    assert len(api.requests) == 1
