# Week 4 Writeup — The Data Explorer MCP Server

## 1. What I built
`server.py` is a `FastMCP` server over STDIO with two tools on the official REST Countries
API:

- `search_countries(query, limit=5)`: the starter's worked example, kept to its original
  contract: a list of `{name, capital, region, population}`, with `[]` for no match.
- `get_country_details(name)`: new. It returns the official name, ISO codes, capital,
  region, population, area, `currencies`, `languages`, `borders` and the flag URLs.

**The starter's API had moved.** The starter called `https://restcountries.com/v3.1`. When
I probed it, every v1, v2, v3, v3.1 and v4 path returned `301` to a JSON notice: *"This API
version has been deprecated … migrate to our new version (v5)."* So the unmodified starter
could not return a single country. Its `httpx.get` doesn't follow redirects, so every call
came back as `upstream request failed`. v5
(`https://api.restcountries.com/countries/v5`) is the only maintained version, and it needs
a free API key (`401 {"code": "authKeyMissing"}` without one). I kept the starter's tool
names and output shapes, and translated v5's response into them:

| Output field | v5 source |
|---|---|
| `name`, `official_name` | `names.common`, `names.official` |
| `capital` | the `capitals[]` entry flagged `attributes.primary` (South Africa has three) |
| `currencies` | `currencies[] → {code, name, symbol}` |
| `languages` | `languages[] → {code: iso639_3, name}` |
| `borders` | `borders` (ISO alpha-3 codes, which the tool also accepts as input) |
| `flag_url`, `flag_svg_url` | `flag.url_png`, `flag.url_svg` |

A second surprise: the starter's `requirements.txt` lists `mcp` unpinned, which now
installs mcp 2.x. That release removed `mcp.server.fastmcp`, so I pinned `mcp>=1.2,<2` to
keep the starter's architecture.

## 2. Transcript: an agent calling both tools

Every call below is real. Nothing is simulated or edited, apart from line wrapping.

### 2a. Claude Code (agent) → `data-explorer`, live REST Countries v5 API
Server registration, captured from `claude mcp get data-explorer`:
```text
data-explorer:
  Scope: User config (available in all your projects)
  Status: ✔ Connected
  Type: stdio
  Command: C:\Users\PC\courses\week4-mcp-data-explorer\.venv\Scripts\python.exe
  Args: C:\Users\PC\courses\week4-mcp-data-explorer\server.py
```

Recorded on 2026-10-15. The agent was Claude Code, and it called the tools through the
registered server (`mcp__data-explorer__*`). `RESTCOUNTRIES_API_KEY` came from the user
environment and does not appear anywhere in the calls or results.

**Call 1: `search_countries`**
```text
→ search_countries {"query": "Germany"}
← {"result": [{"name": "Germany", "capital": "Berlin", "region": "Europe", "population": 83298787}]}
```

**Call 2: `get_country_details`**
```text
→ get_country_details {"name": "Germany"}
← {"name": "Germany", "official_name": "Federal Republic of Germany", "alpha2_code": "DE",
   "alpha3_code": "DEU", "capital": "Berlin", "region": "Europe", "subregion": "Western Europe",
   "population": 83298787, "area_km2": 357114,
   "currencies": [{"code": "EUR", "name": "Euro", "symbol": "€"}],
   "languages": [{"code": "deu", "name": "German"}],
   "borders": ["AUT", "BEL", "CZE", "DNK", "FRA", "LUX", "NLD", "POL", "CHE"],
   "flag_url": "https://flags.restcountries.com/v5/w640/de.png",
   "flag_svg_url": "https://flags.restcountries.com/v5/svg/de.svg", "flag_emoji": "🇩🇪"}
```

**Follow-up calls in the same session: lookup fallbacks and bad input**
```text
→ get_country_details {"name": "DEU"}
← (identical to Call 2: the ISO alpha-3 fallback resolves to Germany)

→ get_country_details {"name": "united"}
← {"error": "'united' matches 7 countries. Call again with one of the candidate names.",
   "code": "ambiguous", "candidates": ["Mexico", "Tanzania", "United Arab Emirates",
   "United Kingdom", "United States", "United States Minor Outlying Islands",
   "United States Virgin Islands"]}

→ search_countries {"query": "Atlantis"}
← {"result": []}

→ search_countries {"query": "   "}
← {"result": [{"error": "query must not be empty", "code": "invalid_input"}]}

→ get_country_details {"name": "../admin?x=1"}
← {"error": "name may contain only letters, spaces and the punctuation - ' . , ( ) &", "code": "invalid_input"}
```
Mexico and Tanzania appear among the `united` candidates because v5 matches official names
too ("United Mexican States", "United Republic of Tanzania"). The `ambiguous` error shows
the agent why, so it can pick the intended country instead of the server guessing.

### 2b. Earlier real calls, before the key was configured
These calls ran the real `server.py` process over STDIO using the MCP Python SDK client
(`mcp.client.stdio`), with no API key configured:
```text
search_countries {"query": "germany"} -> {"result": [{"error": "The REST Countries API key is not configured. Set the RESTCOUNTRIES_API_KEY environment variable for this MCP server (free key: https://restcountries.com/sign-up).", "code": "missing_api_key"}]}
get_country_details {"name": "Germany"} -> {"error": "The REST Countries API key is not configured. Set the RESTCOUNTRIES_API_KEY environment variable for this MCP server (free key: https://restcountries.com/sign-up).", "code": "missing_api_key"}
get_country_details {"name": ""} -> {"error": "name must not be empty", "code": "invalid_input"}
```
I also called the tool functions directly against the **live** v5 API with a deliberately
fake key (`rc_invalid_example_key`):
```text
search  -> [{"error": "REST Countries rejected the API key (No matching authorization key found.). Check RESTCOUNTRIES_API_KEY.", "code": "auth_failed", "status": 401}]
details -> {"error": "REST Countries rejected the API key (No matching authorization key found.). Check RESTCOUNTRIES_API_KEY.", "code": "auth_failed", "status": 401}
```

## 3. How I handled errors
The rule is that **a tool always returns data and never raises**. An exception would
reach the agent as an opaque protocol error, or kill the STDIO server. A structured result
lets the model read what went wrong and decide what to do next. Every error is
`{"error": <sentence for the model>, "code": <stable machine code>}`, plus `status` for
HTTP failures. `search_countries` wraps it in a one-item list, which keeps the starter's
`list` contract.

- **Input is validated before any network call.** The tools reject empty or whitespace
  input, non-strings, over-long input, and characters outside letters, spaces and
  `- ' . , ( ) &`. That allows names like `Côte d'Ivoire` but rejects `../admin` or
  `germany?limit=100`. `limit` must be a real integer from 1 to 25, so `True` and `"5"`
  are rejected. Names placed in URL paths are still percent-encoded.
- **Credentials.** A missing key gives `missing_api_key`, with instructions and no wasted
  request. A malformed key (spaces, control or non-ASCII characters) gives `invalid_api_key`
  before any request; see the live-testing note below. A 401 gives `auth_failed`, including the API's own reason. The key is read from
  the environment on each call and sent only in a header. Error text is scrubbed in case
  the upstream echoes it back. Only the exception *type* of transport errors is reported,
  never their message.
- **HTTP failures each get a distinct code,** so the agent can choose sensibly:
  `rate_limited` (wait and retry), `forbidden` (quota or plan), `upstream_error` (5xx,
  unexpected 4xx, redirects such as the 301s that broke the starter), `timeout` (10 s total,
  5 s to connect) and `network_error`.
- **Malformed responses.** A body that isn't JSON or lacks `data.objects` gives
  `bad_response`; the legacy deprecation envelope is one example. Inside a valid response,
  each field is read defensively, so a sparse country record yields `null` or `[]`
  instead of a `KeyError`.
- **No result or several results.** For search, "no match" stays `[]`, as the starter
  intended, whether the API answers 404 or an empty list. `get_country_details` first tries
  an exact name, then an ISO code, then a search. If nothing matches it returns
  `not_found`, with a hint to use `search_countries`. If several countries match it returns
  `ambiguous` with the `candidates`, so the agent can retry without guessing.
- **Smaller payloads.** Every request sets `response_fields` to just the fields the tool
  returns. That keeps responses small, and it keeps free-plan requests away from premium
  fields, which answer 403.

**Found in live testing.** The first time the key was configured, the stored value turned out
to be a single non-printable character, probably from pressing Ctrl+V in a masked prompt. The
server sent it anyway, and the live API answered with a vague `400` and an empty body. A
reproduction with a non-ASCII value went further and crashed the tool with an uncaught
`UnicodeEncodeError` while httpx built the `Authorization` header. The server now checks the
key's format first and returns `invalid_api_key` without making a request. Regression tests
cover a control character, a non-ASCII character and embedded spaces.

All of this is checked by 94 tests that use mocked `httpx` transports (`python -m pytest
-q`). They cover each failure mode on both tools, plus protocol-level tests over an
in-memory MCP session and a real STDIO subprocess.

## 4. What I learned
Writing an MCP server made it concrete that the tool layer, not the model, decides what
context an agent can reach. The agent sees only the fields I chose to return, through
inputs I chose to accept. A narrow, typed interface with validation and `response_fields`
keeps its context small and relevant, and it keeps it out of things it shouldn't touch,
such as arbitrary URL paths or the API key. Errors are context too: a clear `code` and
message like `ambiguous` with candidates lets the agent recover on its own, while a raw
stack trace or a silent empty result leaves it guessing. Finally, the API's v3.1 shutdown
showed that the context an agent depends on can disappear under it, and only a boundary
that fails loudly and in a structured way makes that something the agent, and I, can
diagnose.
