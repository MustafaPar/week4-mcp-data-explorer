# Data Explorer — MCP Server (Week 4)

A [Model Context Protocol](https://modelcontextprotocol.io) server that wraps the official
[REST Countries](https://restcountries.com) **v5** API and exposes it to an agent as two
tools over **STDIO**:

| Tool | What it does |
|---|---|
| `search_countries(query, limit=5)` | Find countries by full or partial name. |
| `get_country_details(name)` | Currencies, languages, borders and flag URL for one country. |

It is built on the Week 4 starter (`FastMCP` + `httpx`, STDIO transport). Both tools
validate their input and turn every failure (bad input, missing or rejected API key, HTTP
errors, timeouts, malformed responses, no results) into a structured error. They never
crash the server.

> **Why v5 and an API key?** The starter targeted REST Countries v3.1, which has been
> retired. Every v1–v4 request now returns a `301` redirect to a "This API version has
> been deprecated" notice. v5, the only maintained version, requires a free API key. The
> server adapts v5 responses back to the starter's output shape.

## Requirements
- Python 3.10+ (developed and tested on 3.12)
- A free REST Countries API key: https://restcountries.com/sign-up

## Install
```bash
cd week4-mcp-data-explorer
python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate
pip install -r requirements.txt        # runtime: mcp (1.x), httpx
pip install -r requirements-dev.txt    # adds pytest
```
`mcp` is pinned below 2.0. mcp 2.x removed `mcp.server.fastmcp` (it's now `MCPServer`), so
with an unpinned install the starter's `FastMCP` import fails.

## Configure the API key
The server reads the key from the **`RESTCOUNTRIES_API_KEY`** environment variable each
time a tool is called. It sends the key only in the `Authorization: Bearer` header, never
in a URL, a log or a tool result. If the API's error text ever echoes the key back, the
server redacts it.

Set it as a user environment variable, so it never appears in a file in this project:

```powershell
# Windows (PowerShell). Prompts without echoing; then restart your terminal and MCP client.
$k = Read-Host "REST Countries API key" -AsSecureString
[Environment]::SetEnvironmentVariable("RESTCOUNTRIES_API_KEY", [System.Net.NetworkCredential]::new("", $k).Password, "User")
```
```bash
# macOS/Linux: add to ~/.zshrc or ~/.bashrc (outside the repo)
export RESTCOUNTRIES_API_KEY="..."
```
Never put the key in `server.py`, a committed `.env` file, or this README. `.gitignore`
already excludes `.env`.

## Run (STDIO)
```bash
python server.py
```
The process speaks MCP over stdin and stdout and waits for a client. It's normally
started by an MCP client rather than by hand.

## Register in an MCP client

### Claude Code
Use **absolute paths** to the virtual environment's Python and to `server.py`:
```powershell
claude mcp add --scope user --transport stdio data-explorer -- `
  "C:\Users\PC\courses\week4-mcp-data-explorer\.venv\Scripts\python.exe" `
  "C:\Users\PC\courses\week4-mcp-data-explorer\server.py"

claude mcp get data-explorer        # Status: ✔ Connected
```
No `-e` flag is needed. The server inherits `RESTCOUNTRIES_API_KEY` from the environment,
so the key isn't written into `~/.claude.json`. Restart Claude Code after setting the
variable. To remove the server: `claude mcp remove data-explorer -s user`.

### Claude Desktop
Add this to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "data-explorer": {
      "command": "C:\\Users\\PC\\courses\\week4-mcp-data-explorer\\.venv\\Scripts\\python.exe",
      "args": ["C:\\Users\\PC\\courses\\week4-mcp-data-explorer\\server.py"]
    }
  }
}
```

## Tool reference

### `search_countries`
Search countries by name. Matches common, official, alternate and native names,
case-insensitively, including partial names.

| Parameter | Type | Required | Rules |
|---|---|---|---|
| `query` | string | yes | 1–100 characters: letters, spaces and `- ' . , ( ) &` |
| `limit` | integer | no (default `5`) | 1–25 |

**Returns:** a list of `{name, capital, region, population}`, which is the starter's
shape. An empty list `[]` means nothing matched; as in the starter, that isn't an error.
On failure the tool returns a one-item list holding a structured error.

Example, recorded from the live API through Claude Code on 2026-10-15 (full transcript in
[`writeup.md`](writeup.md)):
```jsonc
// input
{"query": "Germany"}
// output
[{"name": "Germany", "capital": "Berlin", "region": "Europe", "population": 83298787}]
```

### `get_country_details`
Get details for one country.

| Parameter | Type | Required | Accepts |
|---|---|---|---|
| `name` | string | yes | Common name (`Germany`), official name (`Federal Republic of Germany`), or ISO code (`DE`, `DEU`) |

The lookup tries, in order: an exact common name, then an ISO alpha-2 or alpha-3 code
(for 2–3 letter inputs), then a name search. The codes in `borders` can be passed
straight back in.

**Returns** (recorded from the live API on 2026-10-15):
```jsonc
// input
{"name": "Germany"}
// output
{
  "name": "Germany",
  "official_name": "Federal Republic of Germany",
  "alpha2_code": "DE",
  "alpha3_code": "DEU",
  "capital": "Berlin",
  "region": "Europe",
  "subregion": "Western Europe",
  "population": 83298787,
  "area_km2": 357114,
  "currencies": [{"code": "EUR", "name": "Euro", "symbol": "€"}],
  "languages": [{"code": "deu", "name": "German"}],
  "borders": ["AUT", "BEL", "CZE", "DNK", "FRA", "LUX", "NLD", "POL", "CHE"],
  "flag_url": "https://flags.restcountries.com/v5/w640/de.png",
  "flag_svg_url": "https://flags.restcountries.com/v5/svg/de.svg",
  "flag_emoji": "🇩🇪"
}
```
A partial name that matches several countries returns an `ambiguous` error with
`candidates` to choose from (recorded live; v5 also matches official names such as "United
Mexican States"):
```json
{"error": "'united' matches 7 countries. Call again with one of the candidate names.", "code": "ambiguous",
 "candidates": ["Mexico", "Tanzania", "United Arab Emirates", "United Kingdom", "United States",
                "United States Minor Outlying Islands", "United States Virgin Islands"]}
```

### Errors
Every error has the form `{"error": "<human-readable message>", "code": "<code>"}`, plus
`status` when an HTTP status is involved. `search_countries` wraps it in a list.

| `code` | When |
|---|---|
| `invalid_input` | Empty, non-string, too long or disallowed characters; `limit` not an integer from 1 to 25. No API call is made. |
| `missing_api_key` | `RESTCOUNTRIES_API_KEY` is unset or blank. No API call is made. |
| `invalid_api_key` | The variable contains spaces, control or non-ASCII characters (usually a bad paste). No API call is made. |
| `auth_failed` | HTTP 401: key missing, unknown, revoked or expired. |
| `forbidden` | HTTP 403: account frozen (monthly quota) or premium field on the free plan. |
| `rate_limited` | HTTP 429: more than 20 requests per 10 seconds. |
| `upstream_error` | Other HTTP errors (4xx, 5xx, unexpected redirects). |
| `timeout` | No response within 10 s (5 s to connect). |
| `network_error` | DNS, connection or protocol failure. |
| `bad_response` | The body isn't JSON or lacks `data.objects`. |
| `not_found` | `get_country_details` only: nothing matched any lookup. |
| `ambiguous` | `get_country_details` only: several countries match; see `candidates`. |

Real responses captured without a key, and with a fake key against the live API:
```json
{"error": "The REST Countries API key is not configured. Set the RESTCOUNTRIES_API_KEY environment variable for this MCP server (free key: https://restcountries.com/sign-up).", "code": "missing_api_key"}
{"error": "REST Countries rejected the API key (No matching authorization key found.). Check RESTCOUNTRIES_API_KEY.", "code": "auth_failed", "status": 401}
```

## Tests
```bash
python -m pytest -q
```
94 tests, all against mocked `httpx` transports, so no network or API key is needed.
They cover:
- success shapes for both tools and the v5 → starter field mapping
- the lookup fallbacks, ambiguous names and no-result cases
- input validation, checking that invalid input never reaches the API
- missing, malformed and rejected keys, HTTP 301/400/401/403/429/500/503, timeouts and connection errors
- malformed bodies, including the legacy deprecation envelope
- that the key never leaks into a tool result
- the tools as an MCP client sees them, over an in-memory session and over a real
  `python server.py` STDIO subprocess

## Project layout
```
server.py              # the MCP server (two tools)
requirements.txt       # runtime dependencies
requirements-dev.txt   # + pytest
tests/                 # mocked unit tests + MCP protocol tests
writeup.md             # transcript, error handling, reflection
```
