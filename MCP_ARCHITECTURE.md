# Irish Census MCP Server — Architecture

Design for an MCP server that exposes the three Irish Census APIs
(documented in [`1926_CENSUS.md`](./1926_CENSUS.md),
[`1901_1911_CENSUS.md`](./1901_1911_CENSUS.md),
[`PRE_FAMINE_CENSUS.md`](./PRE_FAMINE_CENSUS.md)) to an LLM client in a
shape that makes genealogical "find me likely relatives" queries
tractable.

Built on **FastMCP 2** (`fastmcp` on PyPI, docs at gofastmcp.com).

---

## Driving use case

The architecture is justified by being able to answer queries of this
shape:

> "My great-grandfather was Patrick Murphy from Skibbereen, Co. Cork.
> His wife was Mary O'Brien, whose mother Catherine was from Strabane,
> Co. Tyrone. Find me possible relatives."

To answer this, an LLM needs to chain several primitives:

1. Resolve **Skibbereen, Co. Cork** and **Strabane, Co. Tyrone** to
   canonical place keys, *and know which censuses cover each* (Tyrone
   is Northern Ireland, so only 1901 & 1911 — not 1926).
2. Find candidate **Patrick Murphy**s in Cork across 1901, 1911, 1926
   with a fuzzy name match.
3. For each candidate, reconstruct the **household** to verify his
   wife is a Mary (and née O'Brien, when 1901 data is consulted).
4. Find the matching **Mary O'Brien** household in Tyrone in 1901 or
   1911 — look for a Catherine (her mother) in the same record.
5. Walk siblings of both spouses → these are the user's aunts/uncles
   (or great-aunts/uncles), their children are cousins.
6. Surface scan URLs as citations.

The server's job is to make each of these steps a single tool call,
return compact results that don't blow the LLM's context, and let the
LLM do the reasoning.

---

## Tool surface (six tools)

Kept small on purpose. Each tool maps to *something an LLM wants to do
in genealogy*, not 1:1 with the underlying API endpoints. The LLM never
needs to know that there are three different APIs underneath.

### 1. `resolve_place(query: str) -> list[Place]`

Fuzzy place-name resolver. Takes free-text like `"Skibbereen Co Cork"`,
`"Strabane Tyrone"`, `"navan meath"` and returns structured candidates.

```jsonc
[
  {
    "county": "Cork",
    "parish": "Skibbereen",
    "ded": null,
    "townland": null,
    "geocode_prefix": "CO...",
    "available_in": [1901, 1911, 1926],
    "confidence": 0.92
  }
]
```

Crucial for the use case: the `available_in` field tells the LLM
*which censuses can even answer questions about this place*. Tyrone
(and the other five Northern Ireland counties) returns `[1901, 1911]`
only — no false leads in the 1926 corpus.

The underlying place index is built once at server startup by walking
the facets endpoints (`/census/facets`, `/census/facets_c19`,
`/api/census/facets_c26a`) for `county`, `ded`, `parish`, `townland`,
then deduplicating. Fuzzy matching uses `rapidfuzz` over the cached
canonical names.

### 2. `search_people(...) -> SearchResult`

The workhorse. One unified interface across all three corpora.

Parameters (all optional, must provide at least one of name fields):

```
surname:        str       fuzzy by default
first_name:     str       fuzzy by default
year:           int|"all" 1821, 1831, 1841, 1851, 1901, 1911, 1926, or "all"
county:         str       exact (resolve via resolve_place first)
place:          str       resolves to ded/parish/townland automatically
age:            int       exact
age_range:      (lo, hi)  inclusive
sex:            "M"|"F"
religion:       str       normalized — "Roman Catholic", etc.
fuzzy:          bool      default True (__icontains); False = exact
limit:          int       default 20, max 100
page:           int       default 0
```

Behavior:

- If `year="all"` (default), fans out to all three APIs in parallel, then
  **deduplicates plausibly-same-person rows** before returning. Two
  rows are merged when surname matches, first name fuzzy-matches, the
  age delta is consistent with the year delta (±2), and the place is
  within the same county.
- If a corpus can't cover the requested place (e.g. `county="Tyrone"` +
  `year=1926`), it's skipped silently with a `notes` entry in the
  response.
- Returns compact rows only — *no raw API fields*. The LLM gets enough
  to decide what to drill into.

```jsonc
{
  "results": [
    {
      "ref": "1911:...",
      "name": "Patrick Murphy",
      "age": 38,
      "sex": "M",
      "place": "Townland, DED, Cork",
      "relation": "Head of Family",
      "household_key": "1911:...",
      "seen_in": [1901, 1911],
      "score": 0.88
    }
  ],
  "meta": {
    "total": 12,
    "page": 0,
    "more_available": false,
    "queried_corpora": [1901, 1911, 1926],
    "skipped_corpora": [],
    "notes": []
  }
}
```

- `ref` is an opaque string `"<year>:<id>"` that every other tool accepts.
- `household_key` is `"<year>:<image_group>"`.
- `seen_in` is populated post-dedup; a single physical person may have
  refs in 1901+1911 collapsed into one row (the canonical ref is the
  latest census they appear in).

### 3. `get_household(household_key: str) -> Household`

Given a `household_key` from a search result, returns everyone
enumerated together on that form. This is the killer feature for
"find relatives" — one call gets you the whole nuclear family.

```jsonc
{
  "household_key": "1911:...",
  "year": 1911,
  "place": "Townland, DED, Cork",
  "house_number": "7",
  "members": [
    {"ref": "1911:...", "name": "Patrick Murphy", "age": 38, "sex": "M", "relation": "Head of Family", "marriage": "Married", "religion": "Roman Catholic"},
    {"ref": "1911:...", "name": "Mary Murphy",    "age": 34, "sex": "F", "relation": "Wife",           "marriage": "Married", "religion": "Roman Catholic"},
    {"ref": "1911:...", "name": "Michael Murphy", "age":  7, "sex": "M", "relation": "Son",            "marriage": null,      "religion": "Roman Catholic"}
  ],
  "scans": {
    "form_a": ["/scan/1911/nai003096222.pdf", "/scan/1911/nai003096223.pdf"],
    "form_b1": [...]
  }
}
```

For 1926 households, `members` are everyone sharing `image_group`. For
1901/1911 the same. For pre-Famine, where `image_group` doesn't exist,
this tool returns members from the same `first_image` / `folio_num` /
`townland` triple (best-effort grouping).

### 4. `get_person(ref: str) -> Person`

Full detail for one person. Used sparingly — this is the heaviest
payload. Returns all raw + normalized fields and resolves scans.

The LLM should only call this when it needs to display or verify
specific details (occupation, education, marriage_years, etc.) that
the compact search/household tools don't return.

### 5. `find_relatives(ref: str, spread: int = 1) -> FamilyTree`

Higher-level convenience that bundles common chains.

- `spread=0`: returns just the household of `ref` (equivalent to `get_household`)
- `spread=1`: household + the same person located in the adjacent
  census(es). Lets the LLM see the person as a child in 1901 and as an
  adult in 1911, for instance.
- `spread=2`: household + adjacent appearances + best-guess parent
  households in earlier census(es), found by searching for people of
  the right surname and place who could be parents based on age.

`spread=2` returns *candidates* with confidence scores, not assertions.
The LLM decides what to believe.

Response is a tree, not a flat list — preserves context efficiently:

```jsonc
{
  "subject": {"ref": "1926:...", "name": "Patrick Murphy", "age": 53},
  "household_now": { ...same as get_household... },
  "earlier_self": [
    {"ref": "1911:...", "age": 38, "place": "Townland, DED, Cork", "match_score": 0.91},
    {"ref": "1901:...", "age": 28, "place": "Townland, DED, Cork", "match_score": 0.86}
  ],
  "parent_household_candidates": [
    {"household_key": "1901:...", "match_score": 0.78,
     "head": "Michael Murphy (62)", "wife": "Catherine Murphy (58)"}
  ]
}
```

### 6. `get_scan_url(ref: str, form: str = "A") -> ScanRef`

Returns a fetchable PDF URL for the requested form. For 1901/1911 the
form may be `A`, `B1`, `B2`, `N`. For 1926, `A` (individual) or `B`
(household/dwelling). For pre-Famine, only one folio is available.

```jsonc
{
  "url": "https://api-census.nationalarchives.ie/census/image/nai003096222.pdf",
  "form": "Form A",
  "side": "1",
  "year": 1911,
  "note": "URL 307s to a signed Linode URL valid for 30 minutes"
}
```

The server returns the **stable API URL**, not the signed redirect
target. The client follows the redirect at download time.

---

## Worked example: the Patrick Murphy query

How the example query at the top flows through the tool surface. (The
LLM client orchestrates this — the server just makes each step cheap.)

```
1.  resolve_place("Skibbereen Co Cork")
    → [{county: "Cork", parish: "Skibbereen",
        available_in: [1901, 1911, 1926]}]

2.  resolve_place("Strabane Co Tyrone")
    → [{county: "Tyrone", townland: "Strabane",
        available_in: [1901, 1911]}]    # no 1926 — N. Ireland

3.  search_people(surname="Murphy", first_name="Patrick",
                  county="Cork", place="Skibbereen")
    → candidates spanning 1901/1911/1926 (Murphy is the commonest Irish
       surname — expect many; the dedup collapses cross-census matches)

4.  for each candidate ref:
       get_household(candidate.household_key)
       → look for a "Mary" in the household whose relation is "Wife"
       → if found, note her household_key

5.  search_people(surname="O'Brien", first_name="Mary",
                  county="Tyrone", place="Strabane", year=1901)
    → candidate Mary O'Briens in Tyrone in 1901
       (1926 corpus auto-skipped — Tyrone is Northern Ireland)

6.  for each O'Brien candidate:
       get_household(candidate.household_key)
       → look for a "Catherine" as mother/head
       → cross-check the daughter Mary's age matches what we
         derived from step 4

7.  Once the O'Brien household is confirmed, list siblings (Mary's
    brothers/sisters) → those are the user's maternal aunts/uncles.

8.  find_relatives(matching_patrick_ref, spread=2)
    → returns earlier self appearances + parent-household candidates
       on the Murphy side → paternal aunts/uncles, grandparents.

9.  get_scan_url for each pivotal record → user gets citation links.
```

Each step is one tool call. The LLM does the inference and presents the
narrative. The server never tries to *be* a genealogist — it just makes
the right primitives available.

---

## Context-efficiency strategy

The LLM client's context is the scarce resource. Tactics:

1. **Compact-by-default responses.** `search_people` and `get_household`
   return ~6 fields per person, not the 25-field raw row. `get_person`
   is the explicit opt-in for full detail.
2. **Deduplication in `search_people`.** When `year="all"`, plausibly-
   same-person rows from different censuses get merged into one logical
   row with `seen_in: [1901, 1911]`. Saves redundant repetition.
3. **Hard pagination.** Default `limit=20`, `more_available` flag in
   meta, the LLM must explicitly call again for page 2.
4. **Opaque refs.** Returning `"1911:3666567"` instead of nested
   `{year: 1911, id: 3666567}` saves tokens and makes IDs trivially
   round-trippable.
5. **Place resolution is one-shot.** Cache the resolved
   `(county, ded, townland)` in conversation rather than re-resolving
   "Strabane Tyrone" on every search.
6. **Tree-shaped, not flat, results from `find_relatives`.** Nesting
   lets the LLM grasp structure without re-explaining the household.
7. **Server-side trimming.** `religion: null` and other empty fields
   stripped from JSON before return.
8. **Bounded fan-out.** `find_relatives(spread=2)` returns at most ~10
   candidate parent households, ranked by score — not every plausible
   match.

A reasonable budget: a full investigation of the shape above should
fit in ~3–5k tokens of tool-call results, leaving room for the LLM's
reasoning and the eventual answer.

---

## Implementation stack

| Concern | Choice | Why |
| --- | --- | --- |
| Framework | **FastMCP 2** (`fastmcp`) | Decorator-based, async-native, broad transport support, Pydantic schema generation for free |
| HTTP client | `httpx.AsyncClient` | One pooled client; FastMCP tools can be `async def` natively |
| Fuzzy matching | `rapidfuzz` | C-backed, fast, handles surname/place variants well |
| Place index | Static JSON built at startup from facets endpoints | One-time cost; rarely changes |
| Caching | **FastMCP `ResponseCachingMiddleware`** + an `httpx-cache` layer for the National Archives HTTP responses | Two levels: tool-level (LLM repeats a query) and HTTP-level (multiple tool calls hit the same underlying API row) |
| Rate limiting | `asyncio.Semaphore(4)` per host | Polite — single small national archive |
| Logging | `Context.info()` from inside tools | Surfaces "I queried 1911 with surname=Murphy&county=Cork" to the client UI |

### Server skeleton (illustrative — not full code)

```python
from fastmcp import FastMCP, Context
from .clients import CensusGateway

mcp = FastMCP(name="irish-census")
gateway = CensusGateway()  # owns httpx.AsyncClient + place index

@mcp.tool
async def resolve_place(query: str, ctx: Context) -> list[dict]:
    """Resolve a free-text place to canonical (county, ded, townland) candidates."""
    ...

@mcp.tool
async def search_people(
    surname: str | None = None,
    first_name: str | None = None,
    year: int | str = "all",
    county: str | None = None,
    place: str | None = None,
    age: int | None = None,
    age_range: tuple[int, int] | None = None,
    sex: str | None = None,
    religion: str | None = None,
    fuzzy: bool = True,
    limit: int = 20,
    page: int = 0,
    ctx: Context = None,
) -> dict:
    """Search people across 1821-1926 censuses. Returns compact summaries."""
    ...
```

Internally, `CensusGateway` has three thin clients (`Census1926`,
`Census19011911`, `Census19th`) sharing the same `httpx.AsyncClient`,
each translating between the public tool args and the raw API params.

### Project layout

```
irish_historical_census/
├── 1926_CENSUS.md
├── 1901_1911_CENSUS.md
├── PRE_FAMINE_CENSUS.md
├── MCP_ARCHITECTURE.md          <- this file
├── pyproject.toml
├── fastmcp.json                 <- portable server config
└── src/
    └── irish_census_mcp/
        ├── __init__.py
        ├── server.py            <- FastMCP instance + tool registrations
        ├── gateway.py           <- CensusGateway: shared httpx client, fan-out
        ├── api/
        │   ├── c26.py           <- 1926 client (query_c26a, related_images, image_c26)
        │   ├── c1911.py         <- 1901/1911 client (query, image)
        │   └── c19.py           <- pre-Famine client (query_c19)
        ├── places.py            <- place index + fuzzy resolution
        ├── matching.py          <- name/age dedup, parent-candidate scoring
        ├── models.py            <- Pydantic models for tool I/O
        └── normalize.py         <- surname variants, religion strings, etc.
```

### Transports & deployment

- **Local (default):** stdio. Run as `fastmcp run server.py` from a
  Claude Desktop / Claude Code MCP config. Zero ops.
- **Remote:** streamable HTTP. `fastmcp run server.py --transport http
  --port 8765`. Lets multiple clients share one place-index in memory.
- For a hosted demo, wrap behind a small CDN with auth — the upstream
  APIs are open but the *server* doing fan-out at scale is rate-bait.

---

## What this server does NOT try to do

Explicitly out of scope to keep the surface honest:

- **Civil registration records** (births/marriages/deaths). Those live
  at `irishgenealogy.ie` on a separate site with a separate API.
- **Church records** (parish registers, baptisms).
- **Northern Ireland 1926 census**. It exists but is held by PRONI in
  Belfast, not NAI Dublin — different jurisdiction, different access.
- **OCR of the scan PDFs.** The server points to scans; downloading
  and OCR'ing them is the client's job (and the National Archives'
  transcribers have already done it for the structured rows).
- **Conclusive relationship assertions.** `find_relatives(spread=2)`
  returns *candidates* with scores. The LLM presents probabilities;
  the user decides.
- **DNA matching, immigration records, Griffith's Valuation,
  tithe applotments.** Each is its own dataset.

If/when those are added, they should be separate MCP servers — keep
this one focused on the three NAI census APIs.

---

## Open questions for build-time

These will be settled as the server is built; flagging them now so they
don't get hand-waved:

1. **Surname variants.** How aggressive should the surname normalizer
   be? "O'Brien" / "OBrien" / "Brien" — yes. "Smith" / "Smythe" —
   probably. "Smith" / "Smithers" — no. Probably: phonetic match
   (Soundex/Metaphone) as an opt-in `phonetic=True` flag, default off
   to avoid noise.
2. **Cross-census age delta tolerance.** For dedup in `search_people`,
   how tight a band? Initially **±3 years**, since 19C/early-20C ages
   were routinely rounded or guessed.
3. **Place-index refresh.** Built at startup is fine if startup is
   fast (~10s). If the facets endpoints get slow, persist to a local
   file and refresh weekly.
4. **Tool naming convention.** MCP tools surface to the LLM by name.
   `search_people` reads cleaner than `census_search_people`, but if
   composed with other genealogy MCP servers, namespacing helps.
   Recommend: server-name prefix at the MCP-client config level
   rather than baked into tool names.

---

## See also

- [`1926_CENSUS.md`](./1926_CENSUS.md)
- [`1901_1911_CENSUS.md`](./1901_1911_CENSUS.md)
- [`PRE_FAMINE_CENSUS.md`](./PRE_FAMINE_CENSUS.md)
- FastMCP 2 docs: <https://gofastmcp.com>
