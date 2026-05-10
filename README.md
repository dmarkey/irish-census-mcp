# irish-census-mcp

An MCP server that exposes the National Archives of Ireland census APIs
(1821, 1831, 1841, 1851, 1901, 1911, 1926) to LLM clients, with tools
shaped specifically for genealogy queries — finding people, reconstructing
households, and tracing the same person across censuses.

Built on [FastMCP 2](https://gofastmcp.com).

---

## What it does

The three underlying APIs cover different periods, different counties, and
have three different schemas:

| Period | Host | Rows | Coverage |
| --- | --- | --- | --- |
| 1821 / 1831 / 1841 / 1851 | `api-census.nationalarchives.ie` (`/census/query_c19`) | ~497k | Surviving pre-Famine fragments only |
| 1901 / 1911 | `api-census.nationalarchives.ie` (`/census/query`) | ~8.83M | All 32 counties (whole island) |
| 1926 | `c26-api.nationalarchives.ie` (`/api/census/query_c26a`) | ~2.97M | Free State only (26 counties) |

This server hides all three behind six MCP tools that work in terms an
LLM-driven family-tree query needs: places, people, households, relatives.

## The driving use case

> *"My great-grandfather was Patrick Murphy from Skibbereen, Co. Cork.
> His wife was Mary O'Brien, whose mother Catherine was from Strabane,
> Co. Tyrone. Find me possible relatives."*

An LLM client using this server can resolve those places (and know that
Tyrone isn't in the 1926 census because it's in Northern Ireland), search
for candidates across all years, reconstruct households to find a Mary in
Patrick's family, then jump to the O'Brien household in Tyrone to find
Catherine and her other children — all in a handful of compact tool calls.

A full investigation of that shape lands at **~3,200 tokens** of tool
output, leaving the LLM room to reason.

---

## Before you use this — please read

This package is an unofficial client over open APIs operated by the
National Archives of Ireland (NAI). The fact that the APIs are
unauthenticated does **not** mean the data is freely reusable, and it
does not mean you have unlimited rights to the infrastructure.

### What the NAI actually says

The National Archives' [Site Usage Policy](https://nationalarchives.ie/site-usage-policy/)
states that:

> *"copyright in all content contained in this website remains with the
> National Archives."*

and:

> *"The contents of this website may be freely accessed and downloaded
> for personal use."*

but also:

> *"any form of unauthorised reproduction, including the extraction
> and/or storage in any retrieval system or inclusion in any other
> computer program or work is prohibited."*

For publication (any kind), see
[Copies, Publication and Copyright](https://nationalarchives.ie/help-with-research/copies-publication-and-copyright/)
and its [Permission to Publish](https://nationalarchives.ie/help-with-research/copies-publication-and-copyright/permission-to-publish/)
sub-page:

> *"If you wish to use material from the National Archives in
> publications for commercial or non-commercial purposes you must get
> permission."*
>
> *"There is a fee for publication rights."*
>
> Attribution must include *"the correct document reference code."*

### What that means for users of this server

- **Personal genealogical research is explicitly allowed** — searching
  for your own ancestors, browsing the data, downloading scans for your
  own family tree is fine and that's the main use case here.
- **Storing the data systematically may not be allowed.** The "extraction
  and storage in any retrieval system or inclusion in any other computer
  program" clause is broad. Caching a few query results in memory while
  an LLM works through your question is one thing; building a derivative
  database is a different thing and almost certainly needs permission.
- **Publishing any of the records requires permission and a fee** —
  whether commercial or not. This includes blog posts that reproduce
  scans, articles citing transcribed fields, and websites republishing
  search results. Get permission first; cite the document reference code.
- **The data names real people**, including living descendants of those
  enumerated in 1926. Even where copying is legally permitted, treat
  names, addresses, and family relationships with the care you'd give
  any genealogical source. Don't publish derived material about
  identifiable individuals without consent.
- **This server itself is in murky territory** under a strict reading of
  the policy (it does extract data into another computer program at
  request time). The argument that it's a permitted use is that it
  doesn't *store* anything — every call is a fresh pass-through to the
  NAI API, and the LLM client decides what to do with the response. If
  you wrap this server in something that caches, archives, or
  republishes, the argument gets weaker. **If you have any doubt, ask
  the National Archives directly before you ship.**

### Infrastructure etiquette

The National Archives is a small public institution running these APIs
as a public service. They are not a commercial API provider, and the
1926 release coincides with the centennial — they are seeing genuine
research traffic spikes already.

- **Rate-limit yourself.** This server caps per-host concurrency at 4,
  but if you wrap it in a loop or batch script, add your own throttle.
  A few requests per second is fine; sustained tens-of-thousands per
  hour is not.
- **Cache results client-side when you can.** The upstream is
  CloudFront-cached, but repeated identical queries still travel
  through the API. `ResponseCachingMiddleware` is on the roadmap (see
  Known limitations) but not yet wired up — for now, if you're iterating
  on the same query, save the JSON locally. (Caching is a grey area
  under the policy quoted above — keep caches small, ephemeral, and
  scoped to your current research.)
- **Don't scrape the whole corpus.** The ~12 million rows can in
  principle be paginated through, but please don't. If you have a
  legitimate research need for a bulk extract, contact the archive
  directly.
- **Identify yourself.** This client sends a `User-Agent` of
  `irish-census-mcp/0.1`. If you fork or repackage, use your own
  identifier so any problems can be traced to you and not here.
- **Stop on errors.** If the API starts returning 429s, 5xxs, or other
  signs of stress, back off — don't retry tightly. If the National
  Archives ever asks you to stop or to identify yourself, do so
  immediately.

If you publish anything that relies on this server, please link to the
[National Archives 1926 search page](https://nationalarchives.ie/collections/search-the-1926-census/)
so readers can verify against the primary source, and follow the
attribution/permission process before reproducing any records.

---

## Quickstart

Requires Python 3.11+. The project uses [`uv`](https://docs.astral.sh/uv/)
for dependency management.

```bash
git clone <this repo>
cd irish_census_mcp
uv sync
uv run pytest            # 11 live-API smoke tests
```

Run the server over stdio (for Claude Desktop / Claude Code / any MCP client):

```bash
uv run python -m irish_census_mcp
```

or, via the FastMCP CLI:

```bash
uv run fastmcp run src/irish_census_mcp/server.py
```

---

## Connecting from an MCP client

### Claude Desktop / Claude Code

Add to your MCP config (`~/.config/claude/mcp_servers.json` or whatever
your client uses):

```json
{
  "mcpServers": {
    "irish-census": {
      "command": "uv",
      "args": [
        "--directory", "/path/to/irish_census_mcp",
        "run", "python", "-m", "irish_census_mcp"
      ]
    }
  }
}
```

### Remote HTTP transport

For multi-client deployments, run with the streamable HTTP transport:

```bash
uv run fastmcp run src/irish_census_mcp/server.py --transport http --port 8765
```

Clients then point at `http://host:8765/mcp`. Be aware: the upstream
National Archives APIs are open, so the *server* doing fan-out is what
needs rate protection.

---

## Tools

Six tools, all `async`, all accept and return plain JSON.

### `resolve_place(query) -> list[Place]`

Free-text place → canonical `{county, sub_place, available_in, confidence}`.
The `available_in` field tells the LLM which censuses cover this county —
Northern Ireland counties return `[1821..1911]` (no 1926).

```python
resolve_place("Strabane Co Tyrone")
# [{"county": "Tyrone", "sub_place": "Strabane",
#   "available_in": [1821, 1831, 1841, 1851, 1901, 1911], "confidence": 0.95}]
```

### `search_people(...) -> SearchResult`

Person search across all three corpora. Filters: `surname`, `first_name`,
`year`, `county`, `place`, `age`, `age_range`, `sex`, `religion`, `fuzzy`,
`detail`, `limit`, `page`.

When `year="all"` (default), results from different censuses that plausibly
represent the same person are merged into one row with `seen_in: [1901, 1911, 1926]`
and `related_refs` pointing at the merged-in records.

```python
search_people(surname="Murphy", first_name="Patrick", county="Cork", year="all", limit=10)
# {
#   "results": [
#     {"ref": "1926:...", "name": "Patrick Murphy", "age": 58,
#      "place": "Townland, DED, Cork",
#      "relation": "Head", "household_key": "1926:...",
#      "seen_in": [1901, 1911, 1926],
#      "related_refs": ["1911:...", "1901:..."]},
#     ...
#   ],
#   "meta": {"page": 0, "more_available": false, ...}
# }
```

`detail="brief"` cuts each row in half (~46% fewer tokens) — useful when
the LLM is scanning many candidates before drilling in.

### `get_household(household_key) -> Household`

Given a `household_key` from a search row, returns everyone enumerated
together plus scan URLs. Members capped at 30 (returns
`members_truncated: N` for institutional records).

```python
get_household("1926:...")
# {
#   "household_key": "1926:...",
#   "year": 1926,
#   "place": "Townland, DED, Cork",
#   "members": [
#     {"ref": "1926:...", "name": "Patrick Murphy",  "age": 58, "relation": "Head"},
#     {"ref": "1926:...", "name": "Mary Murphy",     "age": 54, "relation": "Wife"},
#     {"ref": "1926:...", "name": "Michael Murphy",  "age": 22, "relation": "Son"},
#     {"ref": "1926:...", "name": "Bridget Murphy",  "age": 18, "relation": "Daughter"}
#   ],
#   "scans": {"form_a": ["https://.../image_c26/...pdf"], "form_b": [...]}
# }
```

### `get_person(ref, include_raw=False) -> Person`

Full normalized record for one person. `include_raw=True` adds the
underlying API row (null-stripped) when the LLM needs fields not exposed
by the normalized projection — `education`, `deafdumb`, `children_born`
for 1911; `folio_num`, `barony` for pre-Famine.

### `find_relatives(ref, spread=1) -> FamilyTree`

Bundles the common chains around one person:

- `spread=0` — just the household
- `spread=1` — + same person in adjacent census years (capped at 3)
- `spread=2` — + best-guess parent-household candidates (capped at 5, scored)

Parent candidates are heuristic (surname match + plausible parental age
band + county match) — the LLM should present them as candidates to
verify, not assertions.

### `get_scan_url(ref, form="A") -> ScanRef`

Returns the stable API URL for a scan PDF. Forms supported: `A`, `B`,
`B1`, `B2`, `N` (1901/1911 has all five; 1926 has A and B; pre-Famine
has one folio). Each URL 307-redirects to a signed Linode URL valid for
30 minutes — pass the API URL to the user, not the redirect target.

---

## How a real query flows

For the Patrick-Murphy-from-Skibbereen query at the top of this README:

1. `resolve_place("Skibbereen Co Cork")` → Cork + sub_place Skibbereen, available in all years.
2. `resolve_place("Strabane Co Tyrone")` → Tyrone + sub_place Strabane, available 1821–1911 (not 1926, because Tyrone is in Northern Ireland).
3. `search_people(surname="Murphy", first_name="Patrick", county="Cork", year="all")` → candidate Patricks; the dedup collapses cross-census appearances into multi-year rows with `seen_in`.
4. For each promising candidate: `get_household(household_key)` → look for a Mary as wife.
5. `search_people(surname="O'Brien", first_name="Mary", county="Tyrone", year="all")` (1926 auto-skipped — Tyrone is NI).
6. For each Mary O'Brien: `get_household` → look for Catherine as her mother.
7. `find_relatives(mary_ref, spread=2)` → siblings of Mary (maternal aunts/uncles) and possible parent households (maternal grandparents).
8. `get_scan_url(ref)` per pivotal record → citation links for the user.

---

## Context-efficiency guarantees

The architecture explicitly budgets for LLM context. Measured response
sizes on real queries:

| Tool call | ~Tokens |
| --- | --- |
| `resolve_place` | ~50 |
| `search_people` (default, 20 rows) | ~700–1,500 |
| `search_people` (`detail="brief"`) | ~400–800 |
| `get_household` (3 members) | ~310 |
| `get_household` (30 members, capped) | ~2,000 |
| `get_person` (default) | ~70 |
| `get_person` (`include_raw=True`) | ~200 |
| `find_relatives` spread=1 | ~475 |
| `find_relatives` spread=2 | ~700 |

Mechanisms:

- Compact-by-default rows (~6 visible fields, not the 25-field raw row).
- `strip_nulls` and `strip_internals` drop empty/internal keys.
- Opaque `"<year>:<id>"` refs save tokens versus nested objects.
- Cross-year dedup with capped `related_refs` (top 3).
- Hard caps: 30 household members, 3 earlier_self, 5 parent candidates.
- `get_person` opt-in raw payload (deep-null-stripped when included).
- `meta` strips zero-count corpora and empty arrays.

A full investigation of the shape above lands at ~3,200 tokens of tool
output.

---

## Project layout

```
irish_census_mcp/
├── README.md                    # this file
├── 1926_CENSUS.md               # 1926 API reference
├── 1901_1911_CENSUS.md          # 1901/1911 API reference
├── PRE_FAMINE_CENSUS.md         # 1821-1851 fragments reference
├── MCP_ARCHITECTURE.md          # design rationale for this server
├── pyproject.toml
├── fastmcp.json                 # FastMCP run configuration
├── uv.lock
├── src/irish_census_mcp/
│   ├── __init__.py
│   ├── __main__.py              # `python -m irish_census_mcp`
│   ├── server.py                # FastMCP instance + 6 @mcp.tool functions
│   ├── gateway.py               # CensusGateway: orchestration, dedup, caps
│   ├── api.py                   # Census1926 / Census19011911 / Census19th
│   ├── places.py                # County list, alias map, fuzzy resolver
│   ├── normalize.py             # Schema flattener (three schemas → one)
│   └── matching.py              # Cross-year dedup + parent-household scoring
└── tests/
    ├── test_live_smoke.py       # 11 tests against the live APIs
    └── test_john_markey_query.py # End-to-end walk-through of the use case
```

The four `*_CENSUS.md` files document the underlying APIs directly — read
those if you want to call the National Archives endpoints without this
MCP server.

---

## Development

```bash
uv sync                          # install
uv run pytest                    # full test suite (live API, ~2s)
uv run pytest tests/test_john_markey_query.py -s  # see the walkthrough
```

The tests hit the real National Archives APIs — they're idempotent reads,
no auth required. Set `SKIP_LIVE=1` to skip them in CI:

```bash
SKIP_LIVE=1 uv run pytest
```

### Verifying the MCP surface

```python
import asyncio
from fastmcp import Client
from irish_census_mcp.server import mcp

async def main():
    async with Client(mcp) as client:
        r = await client.call_tool("resolve_place",
                                   {"query": "Skibbereen Co Cork"})
        print(r.content[0].text)

asyncio.run(main())
```

---

## Known limitations

- **Northern Ireland 1926 census** is held by PRONI in Belfast (separate
  jurisdiction) — not available here.
- **Civil registration** (births/marriages/deaths) and **church records**
  are not in scope. Those live at `irishgenealogy.ie` with a different API.
- **Pre-Famine household reconstruction** is best-effort — the underlying
  schema doesn't have an `image_group`, so we group by `first_image` +
  townland which can miss split households.
- **Surname phonetic matching** is not implemented. `fuzzy=True` does
  substring + Levenshtein, but won't catch e.g. `O'Brien`/`OBrien`/`Brien`
  or `Smith`/`Smyth`. Workaround: search each spelling explicitly.
- **Place-name resolution** does not have a townland index. The
  `sub_place` field is passed through as `__icontains` against both
  `townland` and `ded` (or `parish` for pre-Famine), unioned. Works well
  for common names but can miss obscure townlands with similar names in
  neighbouring DEDs.
- **Parent-household candidate scoring** in `find_relatives(spread=2)` is
  a heuristic (surname + age band + county). Treat as candidates to
  verify against scans, not assertions.
- **No caching middleware yet.** FastMCP ships `ResponseCachingMiddleware`
  but it's not wired up. Repeated calls will repeatedly hit the API. Each
  HTTP response is CDN-cached upstream, so it's cheap, but still not free.

---

## Data attribution and reuse

All census data is provided by the
[National Archives of Ireland](https://nationalarchives.ie). The APIs are
open and unauthenticated, but **the data is not public domain** — check
the National Archives' terms-of-use and reuse policies before any use
beyond personal genealogical research. See the
["Before you use this"](#before-you-use-this--please-read) section above
for specifics.

This server is an **unofficial** client. It is not endorsed by, affiliated
with, or supported by the National Archives of Ireland. Issues with the
underlying data should be reported to the archive directly, not to this
repository.

The 1926 search interface was launched in 2026 for the centennial of the
1926 census, which had been sealed for 100 years.

---

## Publishing

Releases go to PyPI as
[`irish-census-mcp`](https://pypi.org/project/irish-census-mcp/) via a
GitHub Actions workflow ([`.github/workflows/publish.yml`](./.github/workflows/publish.yml))
that runs on push of any `v*` tag.

The workflow uses **PyPI Trusted Publishing** (OIDC) — no API tokens, no
secrets in GitHub. One-time setup on PyPI is required: at
<https://pypi.org/manage/account/publishing/>, add a pending publisher with:

| Field | Value |
| --- | --- |
| PyPI Project Name | `irish-census-mcp` |
| Owner | `dmarkey` |
| Repository name | `irish-census-mcp` |
| Workflow name | `publish.yml` |
| Environment name | `pypi` |

Cutting a release after that is just:

```bash
# 1. Bump the version in pyproject.toml
# 2. Commit
git commit -am "Release v0.2.0"
# 3. Tag and push
git tag v0.2.0
git push origin main --tags
```

The workflow validates that the tag matches `pyproject.toml`'s version
before publishing, so a forgotten bump fails fast rather than shipping
the wrong artefact. Manual `workflow_dispatch` runs build and verify
but skip the publish step (no tag → nothing to ship).

## License

The **code** in this repository is released under the
[MIT License](./LICENSE) (copyright © 2026 David Markey).

This licence covers only the code in this repository. **It does not cover
the underlying census data**, which remains under the National Archives
of Ireland's terms — see
["Before you use this"](#before-you-use-this--please-read).

## See also

- [`MCP_ARCHITECTURE.md`](./MCP_ARCHITECTURE.md) — design doc explaining
  the six-tool surface, context-efficiency strategy, and what's
  deliberately out of scope.
- [`1926_CENSUS.md`](./1926_CENSUS.md), [`1901_1911_CENSUS.md`](./1901_1911_CENSUS.md),
  [`PRE_FAMINE_CENSUS.md`](./PRE_FAMINE_CENSUS.md) — direct API references.
- [FastMCP 2 docs](https://gofastmcp.com)
- [National Archives census search](https://nationalarchives.ie/collections/search-the-1926-census/)
