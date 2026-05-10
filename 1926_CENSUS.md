# Irish 1926 Census — API Reference

Unofficial reverse-engineered notes for the public JSON API behind
`https://nationalarchives.ie/collections/search-the-1926-census/`.

The 1926 Census was the first census of the Irish Free State and was sealed
for 100 years; the records were released to the public in early 2026 along
with this search interface. The frontend is a WordPress page that delegates
all data work to a separate API host operated by Derilinx on behalf of the
National Archives of Ireland.

> **No authentication required.** Everything described here is publicly
> reachable and CORS-allowlisted to `https://nationalarchives.ie`. Be a
> good citizen: rate-limit yourself, cache responses, and identify your
> client with a sensible `User-Agent`.

---

## Hosts

| Census | Host | Notes |
| --- | --- | --- |
| 1926 | `https://c26-api.nationalarchives.ie` | Primary subject of this doc |
| 1901 / 1911 | `https://api-census.nationalarchives.ie` | Older endpoints (`query_c19`, `facets_c19`), similar shape |
| 1926 (staging) | `https://c26.staging.derilinx.com` | Vendor staging — don't hammer |

Backend is FastAPI on `uvicorn` fronted by CloudFront (`X-Cache`,
`X-Amz-Cf-*` headers visible on responses; validation errors come back in
Pydantic v2 format).

---

## Endpoints

### `GET /api/census/query_c26a`

Searches individual person records.

#### Query parameters

Filters use Django-style suffixes. Without a suffix, equality is exact.

| Parameter | Type | Example | Notes |
| --- | --- | --- | --- |
| `surname` | str | `Murphy` | Exact match |
| `surname__icontains` | str | `murph` | Case-insensitive substring |
| `surname__iexact` | str | `murphy` | Case-insensitive exact |
| `first_name` / `first_name__icontains` / `first_name__iexact` | str | | |
| `county` | str | `Cork` | Exact, title-cased; one of the 26 Free State counties |
| `townland` / `townland__icontains` | str | `Coolnagarrane` | |
| `ded` / `ded__icontains` | str | `Skibbereen Rural` | District Electoral Division |
| `updated_sex` | `M` / `F` | `F` | Use the cleaned column |
| `updated_marriage` | str | `Married`, `Single`, `Widow`, `Widower` | Free-text in source; normalized here |
| `updated_religion` | str | `Roman Catholic`, `Church of Ireland`, … | Use facets to discover exact values |
| `updated_irish_language` | str | `English Only`, `English and Irish`, `Irish Only` | |
| `birthplace_county` | str | `Cork` | Often messy in source data |
| `updated_age` | int | `45` | Exact |
| `updated_age__gte` / `updated_age__lte` | int | `80` / `85` | **Use these** for age ranges |
| `age` / `age__gte` / `age__lte` | str/int | | Filter on the raw, often-junk column — prefer `updated_age*` |
| `geocode` | str | `CK26021` | DED-level geographic code |
| `image_group` | int | `194851` | All people on the same form share this |
| `a_id` | int | `2657797` | Per-person primary key |
| `limit` | int | `30` | Page size, default 30 |
| `offset` | int | `60` | Pagination cursor |

The form on the public site only exposes `surname`, `first_name`, `county`,
`townland`, `ded`, plus an "exact match" toggle (switches between
`__icontains` and `__iexact`). Everything else above is available to direct
API callers and is used internally by facet drill-down on the results page.

#### Response

```json
{
  "results": [
    {
      "a_id": 2657797,
      "aform_name": "Coolnagarrane_1952_0003_0015_0_00029.pdf",
      "image_group": 194851,
      "county": "Cork",
      "townland": "Coolnagarrane",
      "ded": "Skibbereen Rural",
      "geocode": "CK26021",
      "first_name": "Denis",
      "surname": "Murphy",
      "relationship_to_head": "Head",
      "updated_relationship_to_head": "Head",
      "updated_sex": "M",
      "updated_age": 65,
      "updated_marriage": "Married",
      "updated_religion": "Roman Catholic",
      "updated_irish_language": "English Only",
      "years_married": "35",
      "irish_or_english": "ENGLISH",
      "birthplace_county": null,
      "children_born_alive": null,
      "children_living": "none",
      "institution_name": null,
      "institution_type": null
    }
  ],
  "meta": {
    "count": 775485,
    "next": "?county=Cork&limit=30&offset=30&surname__icontains=Murphy",
    "prev": null
  }
}
```

**Pagination.** `meta.next` and `meta.prev` are *relative query strings*
(or `null`). Append them to the endpoint path to walk pages:

```
https://c26-api.nationalarchives.ie/api/census/query_c26a{meta.next}
```

`meta.count` is the total matching rows. As of 2026, the corpus is
**~2,972,451 records** (full-population query).

#### Field meanings

| Field | Meaning |
| --- | --- |
| `a_id` | Stable per-person row ID — use as your join key. |
| `aform_name` | Filename of the **Form A** (individual return) PDF. |
| `image_group` | Group ID that links a person to the household's Form A and Form B scans. People in the same household share this. |
| `county`, `townland`, `ded` | Administrative location at time of census. |
| `geocode` | Compact code for DED, e.g. `CK26021` = Cork DED 26021. |
| `relationship_to_head` | Raw transcription. |
| `updated_relationship_to_head` | Cleaned/normalized. |
| `updated_sex` | `M` / `F`. |
| `updated_age` | Integer age. The raw `age` column is often unusable. |
| `updated_marriage` | `Single`, `Married`, `Widow`, `Widower`, `Both Parents Alive` (for minors), etc. |
| `updated_religion` | Normalized denomination. |
| `updated_irish_language` | One of: `English Only`, `English and Irish`, `Irish and English`, `Irish Only`. |
| `years_married` | **Raw string** — frequently contains OCR garbage (`"146"`, `"90976"`). Treat as untrusted. |
| `children_born_alive`, `children_living` | Raw strings — same caveat. |
| `birthplace_county` | Free-text — expect typos (`"Cavanz"`, `"Montagliau"`). |
| `irish_or_english` | Language the form was filled out in (`ENGLISH` / `IRISH`). |
| `institution_name`, `institution_type` | Populated only for residents of institutions (workhouses, schools, asylums, barracks). |

**Form A vs Form B.** Form A is the individual return (one row per person);
Form B is the household / dwelling return (rooms, outbuildings, etc.). The
API only returns Form A rows directly; Form B is reached via `related_images`.

---

### `GET /api/census/facets_c26a`

Returns top-N value counts for several columns, scoped by the same filters
you'd pass to `query_c26a`. Used to populate the sidebar drill-down on the
results page.

```
GET /api/census/facets_c26a?surname__icontains=Murphy&county=Cork
```

Response shape:

```json
[
  {
    "field": "updated_irish_language",
    "counts": [
      {"value": "English Only", "ct": 210},
      {"value": "English and Irish", "ct": 25},
      {"value": "Irish and English", "ct": 15},
      {"value": "Irish Only", "ct": 2}
    ]
  },
  { "field": "townland", "counts": [ … ] }
]
```

Facet fields currently returned: `county`, `ded`, `townland`, `surname`,
`age`, `updated_religion`, `updated_irish_language` (top 20 values each,
top 12 for `age`, top 7 for `updated_religion`).

---

### `GET /api/census/related_images`

Resolves the scan PDFs associated with a household.

Required query parameter: **`image_group`** (integer, from any result row).

```
GET /api/census/related_images?image_group=194851
```

Response:

```json
{
  "aform_names": ["Coolnagarrane_1952_0003_0015_0_00029.pdf"],
  "bform_names": [
    "Coolnagarrane_1952_0003_0001_0_00001.pdf",
    "Coolnagarrane_1952_0003_0001_1_00002.pdf"
  ]
}
```

`aform_names` → individual return(s); `bform_names` → building/dwelling
return pages. Filename convention:

```
<TownlandSlug>_<DED-book>_<DED-no>_<household-no>_<page-index>_<image-no>.pdf
```

(The first segment is human-readable; the rest is the National Archives'
internal pagination scheme — don't try to parse it semantically, treat the
filename as an opaque key.)

A 422 with `{"detail":[{"type":"missing","loc":["query","image_group"], …}]}`
means you forgot the param.

---

### `GET /api/census/image_c26/{filename}`

Returns the actual PDF scan. The frontend Fancybox lightbox embeds these
inline.

```
GET /api/census/image_c26/Coolnagarrane_1952_0003_0015_0_00029.pdf
→ 200 OK
   Content-Type: application/pdf
   ~2 MB
```

- The filename is whatever appears in `aform_name` or in
  `related_images.{aform_names,bform_names}`.
- `HEAD` returns **405 Method Not Allowed** — always use `GET`.
- Responses are CloudFront-cached; identical requests are cheap.

---

## Typical lookup flow

1. **Search** — `query_c26a?surname__icontains=…&county=…` → list of people.
2. **Drill down** — for each row, note `image_group` to group household
   members.
3. **Get scans** — `related_images?image_group=<n>` → filenames for the
   household's Form A and Form B pages.
4. **Download** — `image_c26/<filename>` for each PDF you actually want.
5. **Paginate** — append `meta.next` to step 1's URL until it goes `null`.

---

## Worked example

Find all Murphys in Cork, fetch the household scan for the first hit:

```bash
BASE=https://c26-api.nationalarchives.ie/api/census

# 1. Search
curl -s "$BASE/query_c26a?surname__icontains=Murphy&county=Cork&limit=1"

# 2. Pick the image_group from results[0], e.g. 194851
curl -s "$BASE/related_images?image_group=194851"

# 3. Download the Form A PDF
curl -s -o household.pdf \
  "$BASE/image_c26/Coolnagarrane_1952_0003_0015_0_00029.pdf"
```

Reconstruct a household: query with `image_group=<n>` (no other filters) —
all rows returned are people enumerated together on that form.

---

## Data quality notes

The "raw" columns (`age`, `years_married`, `children_born_alive`,
`children_living`, `relationship_to_head`, `birthplace_county`) are direct
HTR/OCR output and are **noisy**. Examples from real data:

- `"years_married": "146"` (impossible)
- `"children_born_alive": "Zen"` (probably "Ten")
- `"birthplace_county": "Corkz"` (smudged "Cork")
- `"relationship_to_head": "Daughter in-Low"`

Columns prefixed `updated_` have been normalized — prefer them for
filtering and analysis. When the `updated_*` value is `null`, fall back to
the raw column with caution.

`children_living` sometimes contains running annotations
(`"Three 3 Daughter."`, `"Residing as members of this"`) where the
transcriber captured a hand-written note rather than a number. Don't
parse it as numeric.

---

## Operational details

- **CORS:** `Access-Control-Allow-Origin: https://nationalarchives.ie`
  with credentials. CORS is browser-enforced — server-side clients
  (curl, Python `requests`, …) get the data regardless of `Origin`.
- **CDN:** CloudFront edge in Dublin (`DUB56-P1`). Most query responses
  cache well; `image_c26` PDFs cache very well.
- **Rate limits:** none observed up to modest hand-issued bursts.
  Sustained scraping will likely upset someone — throttle to ≤ a few
  RPS and back off on 5xx.
- **Errors:** Pydantic validation errors return `422` with a `detail`
  array. Missing files return `404` with a JSON `{"detail":"…"}` body.
  `HEAD` on `image_c26` returns `405`.
- **HTTPS only:** plain HTTP redirects are not exposed publicly.

---

## Related surfaces

- **Map view:** `https://c26-api.nationalarchives.ie/map.html` — a
  separate single-page app that visualises results by `geocode`/DED.
- **1901 / 1911 censuses:** see [`1901_1911_CENSUS.md`](./1901_1911_CENSUS.md).
  Different host (`api-census.nationalarchives.ie`), different schema
  (e.g. `firstname` not `first_name`), and images come embedded in the
  query response — no follow-up `related_images` call needed.
- **Pre-Famine fragments (1821, 1831, 1841, 1851):** see
  [`PRE_FAMINE_CENSUS.md`](./PRE_FAMINE_CENSUS.md). Same host as
  1901/1911 but a separate endpoint (`/census/query_c19`) and a heavily
  different schema (no DED, religion as household counts rather than
  per-person, much smaller corpus due to the 1922 Four Courts fire).
