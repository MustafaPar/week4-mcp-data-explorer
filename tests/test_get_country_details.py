"""get_country_details: return shape, lookup strategy, and no-result/ambiguous cases."""
from __future__ import annotations

import httpx
import pytest

import server
from conftest import GERMANY, MockApi, country, envelope

EXPECTED_GERMANY = {
    "name": "Germany",
    "official_name": "Federal Republic of Germany",
    "alpha2_code": "DE",
    "alpha3_code": "DEU",
    "capital": "Berlin",
    "region": "Europe",
    "subregion": "Western Europe",
    "population": 83491249,
    "area_km2": 357114,
    "currencies": [{"code": "EUR", "name": "Euro", "symbol": "€"}],
    "languages": [{"code": "deu", "name": "German"}],
    "borders": ["AUT", "BEL", "CZE", "DNK", "FRA", "LUX", "NLD", "POL", "CHE"],
    "flag_url": "https://flags.restcountries.com/v5/w640/de.png",
    "flag_svg_url": "https://flags.restcountries.com/v5/svg/de.svg",
    "flag_emoji": "🇩🇪",
}


def test_exact_name_returns_full_details(api: MockApi) -> None:
    api.respond_by_path({"/names.common/germany": httpx.Response(200, json=envelope(GERMANY))})

    assert server.get_country_details("germany") == EXPECTED_GERMANY
    (request,) = api.requests
    assert request.url.params["response_fields"] == server.DETAIL_FIELDS


def test_name_is_url_encoded_in_the_path(api: MockApi) -> None:
    api.respond_json(envelope(country("Côte d'Ivoire")))

    server.get_country_details("Côte d'Ivoire")

    assert api.requests[0].url.raw_path.startswith(b"/countries/v5/names.common/C%C3%B4te%20d%27Ivoire?")


@pytest.mark.parametrize(("code", "path"), [("DEU", "/codes.alpha_3/DEU"), ("de", "/codes.alpha_2/DE")])
def test_falls_back_to_iso_code(api: MockApi, code: str, path: str) -> None:
    api.respond_by_path({path: httpx.Response(200, json=envelope(GERMANY))})

    assert server.get_country_details(code)["name"] == "Germany"
    assert [r.url.path for r in api.requests] == [f"/countries/v5/names.common/{code}", f"/countries/v5{path}"]


def test_falls_back_to_search_for_official_name(api: MockApi) -> None:
    api.respond_by_path({"/name": httpx.Response(200, json=envelope(GERMANY))})

    result = server.get_country_details("Federal Republic of Germany")

    assert result["name"] == "Germany"
    search = api.requests[-1]
    assert search.url.path == "/countries/v5/name"
    assert search.url.params["q"] == "Federal Republic of Germany"


def test_single_partial_match_is_returned(api: MockApi) -> None:
    api.respond_by_path({"/name": httpx.Response(200, json=envelope(GERMANY))})

    assert server.get_country_details("germ")["alpha3_code"] == "DEU"


def test_ambiguous_partial_name_lists_candidates(api: MockApi) -> None:
    api.respond_by_path(
        {"/name": httpx.Response(200, json=envelope(country("United States"), country("United Kingdom")))}
    )

    result = server.get_country_details("united")

    assert result["code"] == "ambiguous"
    assert result["candidates"] == ["United States", "United Kingdom"]


def test_one_exact_match_among_several_wins(api: MockApi) -> None:
    api.respond_by_path(
        {"/name": httpx.Response(200, json=envelope(country("Guinea-Bissau"), country("Guinea"), country("Papua New Guinea")))}
    )

    assert server.get_country_details("Guinea")["name"] == "Guinea"


def test_not_found_after_all_lookups(api: MockApi) -> None:
    api.respond_by_path({"/name": httpx.Response(200, json=envelope())})

    result = server.get_country_details("Atlantis")

    assert result["code"] == "not_found"
    assert "Atlantis" in result["error"]
    assert len(api.requests) == 2  # exact name, then search (8 letters: no ISO-code attempt)


def test_sparse_country_object_does_not_crash(api: MockApi) -> None:
    api.respond_json(
        envelope(
            {
                "names": {"common": "Bouvet Island"},
                "capitals": "not-a-list",
                "currencies": [None, {"code": "NOK"}],
                "languages": None,
                "borders": ["NOR", 7],
                "flag": None,
            }
        )
    )

    result = server.get_country_details("Bouvet Island")

    assert result["name"] == "Bouvet Island"
    assert result["capital"] is None
    assert result["currencies"] == [{"code": "NOK", "name": None, "symbol": None}]
    assert result["languages"] == []
    assert result["borders"] == ["NOR"]
    assert result["flag_url"] is None


@pytest.mark.parametrize(
    ("name", "message"),
    [("", "must not be empty"), ("  ", "must not be empty"), (None, "must be a string"), ("a/b", "may contain only")],
)
def test_invalid_name_never_calls_the_api(api: MockApi, name: object, message: str) -> None:
    result = server.get_country_details(name)  # type: ignore[arg-type]

    assert result["code"] == "invalid_input"
    assert message in result["error"]
    assert api.requests == []
