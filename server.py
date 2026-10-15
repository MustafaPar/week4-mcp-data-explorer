"""Data Explorer — an MCP server for the REST Countries API (Week 4).

Wraps the official REST Countries **v5** API (https://restcountries.com) and exposes it
to an agent as two MCP tools over STDIO:

* ``search_countries(query, limit)`` — find countries by (partial) name.
* ``get_country_details(name)`` — currencies, languages, borders and flag for one country.

The starter targeted v3.1, which REST Countries has retired (every v1–v4 request now
redirects to a deprecation notice). v5 needs a free API key, which this server reads
from the ``RESTCOUNTRIES_API_KEY`` environment variable at call time. The key is only
ever sent in the ``Authorization`` header; it is never logged or returned to the model.

Both tools always return data, never raise: failures come back as structured errors
of the form ``{"error": <message>, "code": <machine-readable code>, ...}``.

Setup:
    pip install -r requirements.txt
Run (STDIO) — register this command in your MCP client:
    python server.py
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import quote

import httpx
from mcp.server.fastmcp import FastMCP

BASE_URL = "https://api.restcountries.com/countries/v5"
API_KEY_ENV = "RESTCOUNTRIES_API_KEY"
TIMEOUT = httpx.Timeout(10.0, connect=5.0)

MAX_QUERY_LENGTH = 100
MAX_LIMIT = 25  # keep tool output small; the API itself allows up to 100 on the free plan

# Ask only for the fields each tool returns. Smaller payloads, and it keeps free-plan
# requests away from premium fields (such as `leaders`), which answer 403.
SEARCH_FIELDS = "names.common,capitals,region,population"
DETAIL_FIELDS = (
    "names.common,names.official,codes.alpha_2,codes.alpha_3,capitals,region,subregion,"
    "population,area,currencies,languages,borders,flag.url_png,flag.url_svg,flag.emoji"
)

# Punctuation that appears in real country names: "Guinea-Bissau", "Côte d'Ivoire",
# "St. Lucia", "Congo (Kinshasa)", "Bosnia and Herzegovina", "Saint Helena, Ascension…".
_NAME_PUNCTUATION = set(" -'’.,()&")

mcp = FastMCP("data-explorer")

# httpx logs every request URL at INFO; keep query terms out of client logs.
logging.getLogger("httpx").setLevel(logging.WARNING)


class ApiError(Exception):
    """A failed upstream call, already translated into a model-friendly error."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        error: dict[str, Any] = {"error": self.message, "code": self.code}
        if self.status is not None:
            error["status"] = self.status
        return error


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"error": message, "code": code, **extra}


# --------------------------------------------------------------------------- input


def _validate_name(value: Any, field: str) -> str | dict[str, Any]:
    """Return the cleaned name, or a structured `invalid_input` error."""
    if not isinstance(value, str):
        return _error("invalid_input", f"{field} must be a string")
    cleaned = " ".join(value.split())  # trim and collapse internal whitespace
    if not cleaned:
        return _error("invalid_input", f"{field} must not be empty")
    if len(cleaned) > MAX_QUERY_LENGTH:
        return _error("invalid_input", f"{field} must be at most {MAX_QUERY_LENGTH} characters")
    if not any(ch.isalpha() for ch in cleaned) or any(
        not (ch.isalpha() or ch in _NAME_PUNCTUATION) for ch in cleaned
    ):
        return _error(
            "invalid_input",
            f"{field} may contain only letters, spaces and the punctuation - ' . , ( ) &",
        )
    return cleaned


def _validate_limit(limit: Any) -> int | dict[str, Any]:
    # bool is a subclass of int; `limit=True` is a mistake, not 1.
    if isinstance(limit, bool) or not isinstance(limit, int):
        return _error("invalid_input", "limit must be an integer")
    if not 1 <= limit <= MAX_LIMIT:
        return _error("invalid_input", f"limit must be between 1 and {MAX_LIMIT}")
    return limit


# ---------------------------------------------------------------------------- HTTP


def _make_client() -> httpx.Client:
    """Build the HTTP client. Tests replace this to inject a mock transport."""
    return httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=False)


def _api_key() -> str:
    key = os.environ.get(API_KEY_ENV, "").strip()
    if not key:
        raise ApiError(
            "missing_api_key",
            f"The REST Countries API key is not configured. Set the {API_KEY_ENV} "
            "environment variable for this MCP server (free key: https://restcountries.com/sign-up).",
        )
    # A key is printable ASCII with no spaces. Anything else is usually a bad paste (for
    # example a control character), and non-ASCII would make httpx raise when building
    # the Authorization header.
    if not (key.isascii() and key.isprintable()) or any(ch.isspace() for ch in key):
        raise ApiError(
            "invalid_api_key",
            f"The {API_KEY_ENV} environment variable is malformed (it contains spaces, "
            "control or non-ASCII characters; it may have been pasted incorrectly). Set it again.",
        )
    return key


def _upstream_message(resp: httpx.Response) -> str | None:
    """Extract the API's own error message, if the body has one (shortened, key redacted)."""
    try:
        errors = resp.json().get("errors")
        message = str(errors[0]["message"])
    except Exception:
        return None
    key = os.environ.get(API_KEY_ENV, "").strip()
    if key:
        message = message.replace(key, "[REDACTED]")
    return message[:200] or None


def _raise_for_status(resp: httpx.Response) -> None:
    status = resp.status_code
    if 200 <= status < 300:
        return
    detail = _upstream_message(resp)
    suffix = f" ({detail})" if detail else ""
    if status == 401:
        raise ApiError(
            "auth_failed",
            f"REST Countries rejected the API key{suffix}. Check {API_KEY_ENV}.",
            status,
        )
    if status == 403:
        raise ApiError(
            "forbidden",
            f"REST Countries refused the request; the account may be over its monthly "
            f"quota or the field needs a paid plan{suffix}.",
            status,
        )
    if status == 429:
        raise ApiError(
            "rate_limited",
            "REST Countries rate limit reached (20 requests per 10 seconds). Wait and retry.",
            status,
        )
    if 300 <= status < 400:
        raise ApiError(
            "upstream_error", f"REST Countries redirected the request (HTTP {status}); the API may have moved.", status
        )
    if status >= 500:
        raise ApiError("upstream_error", f"REST Countries server error (HTTP {status}). Try again later.", status)
    raise ApiError("upstream_error", f"REST Countries returned HTTP {status}{suffix}.", status)


def _fetch(path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    """GET a v5 endpoint and return its ``data.objects`` list.

    A 404 means "nothing matched" and returns ``[]``, as the starter treated it. Every
    other failure raises ApiError with a model-friendly message.
    """
    headers = {"Authorization": f"Bearer {_api_key()}", "Accept": "application/json"}
    try:
        with _make_client() as client:
            resp = client.get(path, params=params, headers=headers)
    except httpx.TimeoutException:
        raise ApiError("timeout", "REST Countries did not respond in time. Try again later.") from None
    except httpx.HTTPError as exc:
        # Report only the exception type: messages can contain request details.
        raise ApiError("network_error", f"Could not reach REST Countries ({type(exc).__name__}).") from None

    if resp.status_code == 404:
        return []
    _raise_for_status(resp)

    try:
        objects = resp.json()["data"]["objects"]
    except (ValueError, KeyError, TypeError):
        raise ApiError("bad_response", "REST Countries returned a response in an unexpected format.") from None
    if not isinstance(objects, list):
        raise ApiError("bad_response", "REST Countries returned a response in an unexpected format.")
    return [obj for obj in objects if isinstance(obj, dict)]


# ----------------------------------------------------------------------- shaping


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _primary_capital(country: dict[str, Any]) -> str | None:
    capitals = [c for c in _list(country.get("capitals")) if isinstance(c, dict) and c.get("name")]
    for capital in capitals:  # prefer the one the API flags as primary
        if _dict(capital.get("attributes")).get("primary"):
            return capital["name"]
    return capitals[0]["name"] if capitals else None


def _summary(country: dict[str, Any]) -> dict[str, Any]:
    """The starter's search result shape: {name, capital, region, population}."""
    return {
        "name": _dict(country.get("names")).get("common"),
        "capital": _primary_capital(country),
        "region": country.get("region"),
        "population": country.get("population"),
    }


def _details(country: dict[str, Any]) -> dict[str, Any]:
    names = _dict(country.get("names"))
    codes = _dict(country.get("codes"))
    flag = _dict(country.get("flag"))
    return {
        "name": names.get("common"),
        "official_name": names.get("official"),
        "alpha2_code": codes.get("alpha_2"),
        "alpha3_code": codes.get("alpha_3"),
        "capital": _primary_capital(country),
        "region": country.get("region"),
        "subregion": country.get("subregion"),
        "population": country.get("population"),
        "area_km2": _dict(country.get("area")).get("kilometers"),
        "currencies": [
            {"code": c.get("code"), "name": c.get("name"), "symbol": c.get("symbol")}
            for c in _list(country.get("currencies"))
            if isinstance(c, dict)
        ],
        "languages": [
            {"code": lang.get("iso639_3") or lang.get("iso639_1"), "name": lang.get("name")}
            for lang in _list(country.get("languages"))
            if isinstance(lang, dict)
        ],
        "borders": [b for b in _list(country.get("borders")) if isinstance(b, str)],
        "flag_url": flag.get("url_png"),
        "flag_svg_url": flag.get("url_svg"),
        "flag_emoji": flag.get("emoji"),
    }


def _same_name(country: dict[str, Any], name: str) -> bool:
    names = _dict(country.get("names"))
    wanted = name.casefold()
    return any(isinstance(n, str) and n.casefold() == wanted for n in (names.get("common"), names.get("official")))


# -------------------------------------------------------------------------- tools


@mcp.tool()
def search_countries(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Search countries by name. Returns a list of {name, capital, region, population}.

    Matches common, official, alternate and native names, case-insensitively, including
    partial names ("united" finds the United States, the United Kingdom, ...). Returns at
    most `limit` results (1-25, default 5). An empty list means nothing matched.
    On failure, returns a one-item list: [{"error": <message>, "code": <code>}].
    """
    cleaned = _validate_name(query, "query")
    if isinstance(cleaned, dict):
        return [cleaned]
    checked_limit = _validate_limit(limit)
    if isinstance(checked_limit, dict):
        return [checked_limit]

    try:
        countries = _fetch(
            "/name", {"q": cleaned, "limit": checked_limit, "response_fields": SEARCH_FIELDS}
        )
    except ApiError as exc:
        return [exc.to_dict()]
    return [_summary(c) for c in countries[:checked_limit]]


@mcp.tool()
def get_country_details(name: str) -> dict[str, Any]:
    """Get details for one country: currencies, languages, borders and flag URL.

    `name` is a country's common or official name ("Germany", "Federal Republic of
    Germany"), or its 2- or 3-letter ISO code ("DE", "DEU") — the codes listed in
    `borders` can be passed straight back in. If the name is partial and matches several
    countries, returns an error with code "ambiguous" and a list of `candidates` to pick from.
    On failure, returns {"error": <message>, "code": <code>}.
    """
    cleaned = _validate_name(name, "name")
    if isinstance(cleaned, dict):
        return cleaned

    params = {"response_fields": DETAIL_FIELDS}
    try:
        # 1. Exact common name, e.g. "germany" -> Germany.
        matches = _fetch(f"/names.common/{quote(cleaned, safe='')}", params)
        # 2. ISO code, e.g. "DEU" (borders are reported as alpha-3 codes).
        if not matches and cleaned.isalpha() and len(cleaned) in (2, 3):
            field = "alpha_2" if len(cleaned) == 2 else "alpha_3"
            matches = _fetch(f"/codes.{field}/{quote(cleaned.upper(), safe='')}", params)
        # 3. Partial or official name, e.g. "Federal Republic of Germany".
        if not matches:
            matches = _fetch("/name", {**params, "q": cleaned, "limit": 10})
    except ApiError as exc:
        return exc.to_dict()

    if not matches:
        return _error("not_found", f"No country matches '{cleaned}'. Try search_countries to find the exact name.")
    exact = [c for c in matches if _same_name(c, cleaned)]
    if len(matches) > 1 and len(exact) != 1:
        candidates = [_dict(c.get("names")).get("common") for c in matches]
        return _error(
            "ambiguous",
            f"'{cleaned}' matches {len(matches)} countries. Call again with one of the candidate names.",
            candidates=[c for c in candidates if c],
        )
    return _details(exact[0] if exact else matches[0])


if __name__ == "__main__":
    mcp.run()
