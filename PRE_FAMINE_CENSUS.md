# Irish Pre-Famine Census Fragments — API Reference

Unofficial reverse-engineered notes for the National Archives of Ireland's
public JSON API covering the surviving **pre-Famine and Famine-era**
census fragments: **1821, 1831, 1841, 1851**.

## Why this is so small

Most of Ireland's 19th-century enumerators' returns were destroyed in the
**Four Courts fire of 1922** during the Civil War. What survives is a
patchwork of:

- Parish-level transcripts that local clergy made before the originals
  were sent to Dublin
- Returns from specific townlands that got separated from the main
  collection
- A small set of 1851 forms that recorded deaths since 1841 (used for
  pension claims after independence and so kept separately)

The result: **497,384 total rows**, heavily skewed to a few northern and
midland counties.

| Census year | Surviving rows |
| --- | --- |
| 1821 | 276,407 |
| 1831 | 80,997 |
| 1841 | 16,249 |
| 1851 | 123,731 |
| 1861/1871/1881/1891 | **0** (destroyed in 1922) |

Top surviving counties: Cavan (176,518), Londonderry (79,028), Antrim
(62,260). Many counties have only a handful of rows or none at all —
Monaghan has just 15 surviving records across all four censuses.

> **No authentication required.** CORS is open
> (`Access-Control-Allow-Origin: *`).

---

## Host

```
https://api-census.nationalarchives.ie
```

Same host as the 1901/1911 API — different endpoint suffix.

---

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /census/query_c19` | Person search |
| `GET /census/facets_c19` | Facet counts |
| `GET /census/image/{nai_id}.pdf` | Scan PDF (shared with 1901/1911 API) |

The `_c19` suffix is the National Archives' internal label for the
19th-century-fragments corpus.

---

## `GET /census/query_c19`

### Query parameters

Same Django-style filtering as the other census APIs.

| Parameter | Type | Example | Notes |
| --- | --- | --- | --- |
| `surname` / `surname__icontains` / `surname__iexact` | str | `Reilly` | |
| `firstname` / `firstname__icontains` | str | `Patrick` | One word |
| `census_year` | `1821` \| `1831` \| `1841` \| `1851` | `1821` | |
| `county` | str | `Cavan` | 1820s-era names — some don't match modern usage |
| `barony` | str | `Tullyhunco` | Pre-DED administrative division |
| `parish` | str | `Drung & Larah` | Civil parish |
| `townland` / `townland__icontains` | str | `Trim` | |
| `house_number` | str | `25` | |
| `sex` | `M` / `F` | `F` | Often null in 1821 data |
| `age` / `age__gte` / `age__lte` | str/int | `30` | **String column** in this corpus — `__gte`/`__lte` work as numeric where parseable |
| `age_in_mo` | int | | Age in months (for infants) |
| `occupation` / `occupation__icontains` | str | `Labourer` | |
| `education` | str | | |
| `relation_to_head` | str | | Often null |
| `marriage_status` / `marital_status` | str | | Both fields exist (different forms used different labels) |
| `year_married` | str | | |
| `hoh_flag` | bool | `true` | **Head of household** — useful to deduplicate to one row per family |
| `folio_num` | str | `9` | Folio/page number in the original ledger |
| `families_in_each` | int | | House contained N families |
| `males_in_family` / `females_in_family` | int | | Household composition counts |
| `male_servants` / `female_servants` | int | | |
| `established_church` | int | | Religion **counts** (see note below) |
| `roman_catholics` | int | | |
| `presbyterians` | int | | |
| `other_protestants` | int | | |
| `cause_of_death` | str | | 1851 only |
| `year_of_death` | str | | 1851 only |
| `exceptions` | str | `Blank Image` | Flags from the transcription process |
| `id` | int | | Per-person primary key |
| `limit` / `offset` | int | | Pagination |

### Religion as counts, not per-person

This is the biggest schema surprise. The 1821 and 1831 enumerator forms
asked the **head of household** to report a tally of each denomination
present, not the religion of each individual:

- `established_church` — count of Church of Ireland adherents
- `roman_catholics` — count of Roman Catholics
- `presbyterians` — count of Presbyterians
- `other_protestants` — count of other Protestant denominations

These fields are populated on the **head-of-household row** (`hoh_flag: true`)
and typically null on dependant rows. If you want per-person religion
data this old, it doesn't exist — pick the household head's row and infer.

### Response

```json
{
  "results": [
    {
      "id": 176,
      "census_year": 1821,
      "hoh_flag": false,
      "folio_num": null,
      "county": "Meath",
      "barony": null,
      "parish": "Trim",
      "townland": "Manorland, River Boyne",
      "house_number": "2",
      "firstname": "Patrick",
      "surname": "Reilly",
      "age": "45",
      "sex": null,
      "relation_to_head": null,
      "education": null,
      "occupation": "Haxter & Dairyman",
      "marriage_status": null,
      "marital_status": null,
      "year_married": null,
      "families_in_each": null,
      "males_in_family": null,
      "females_in_family": null,
      "male_servants": null,
      "female_servants": null,
      "established_church": null,
      "roman_catholics": null,
      "presbyterians": null,
      "other_protestants": null,
      "age_in_mo": null,
      "cause_of_death": null,
      "year_of_death": null,
      "first_image": "007246483_00088",
      "last_image": null,
      "exceptions": ""
    }
  ],
  "meta": {
    "count": 11668,
    "next": "?limit=1&offset=1",
    "prev": null
  }
}
```

Note: `occupation: "Haxter & Dairyman"` is the literal transcription —
the original handwriting is illegible (likely "Baxter"). The pre-Famine
records are full of these OCR artefacts.

### No embedded `images` array

Unlike the 1901/1911 API, c19 rows don't get an `images` array. Instead,
two **bare image IDs** are returned:

- `first_image` — opening folio of the household / record group
- `last_image` — closing folio (often null when the record spans a
  single image)

To fetch the scan, append `.pdf` to the ID and hit the shared image
endpoint:

```
GET /census/image/007246483_00088.pdf
```

---

## `GET /census/facets_c19`

Same shape as the 1901/1911 `facets` endpoint, scoped to whatever
filters you pass.

Returned facet fields (top 20 values each):

```
county, barony, parish, townland, surname, occupation, age
```

No religion facets (data shape doesn't suit them) and no DED (the
division didn't exist yet — DEDs were introduced in 1898).

```json
[
  { "field": "county",     "counts": [{"value": "Cavan", "ct": 176518}, ...] },
  { "field": "occupation", "counts": [{"value": "Labourer", "ct": 31204}, {"value": "Farmer", "ct": 24871}, {"value": "Spinner", "ct": 16308}, ...] },
  ...
]
```

("Spinner" being the third-most-common occupation reflects the pre-Famine
linen industry — most rural women in Ulster were doing piecework
spinning at home.)

---

## Typical lookup flow

```bash
BASE=https://api-census.nationalarchives.ie/census

# 1. Search the fragments
curl -s "$BASE/query_c19?surname__icontains=Reilly&census_year=1821&limit=10"

# 2. (Optional) Find the head of household for a parish to get religion counts
curl -s "$BASE/query_c19?parish=Drung+%26+Larah&hoh_flag=true&limit=20"

# 3. Download the scan — note the .pdf suffix, the bare ID returns 404
curl -sL -o reilly_1821.pdf \
  "$BASE/image/007246483_00088.pdf"
```

The image endpoint shares all the redirect behavior described in
[`1901_1911_CENSUS.md`](./1901_1911_CENSUS.md) (307 to a signed Linode
URL, 30-minute expiry, Amsterdam region). The Linode keys for these
scans live under `nai-census/1901-11/2024_scans/` — they were digitised
in 2024 as part of the centennial preparation, even though the data is
much older than the 1901–1911 corpus they share storage with.

---

## Data quality and historical notes

- **`age` is a string**, not an integer — 1820s forms accepted "about 50",
  "3 months", "Inf<sup>t</sup>" etc., and the transcription preserves them.
  Use `age_in_mo` for parseable infant ages.
- **`sex` is often null** in 1821 returns — many enumerators recorded it
  positionally (males in one column, females in another) rather than as
  a labelled field, and that structure didn't survive transcription.
- **`occupation`** is the most historically interesting column. It
  reflects the pre-industrial rural economy: Labourer, Farmer, Spinner,
  Weaver, Servant. Watch for OCR / transcription artefacts ("Infermary",
  "Spinster" vs "Spinner").
- **Spellings are inconsistent** — surnames may appear as `Reilly Or O Reilly`
  where the enumerator wasn't sure. The transcribers preserved these.
- **`exceptions: "Blank Image"`** rows are placeholders for known-missing
  pages. They carry minimal data.
- **County boundaries** are 1820s definitions, mostly stable to modern
  ones except in the Dublin/Wicklow border and a few Ulster precincts.

---

## Operational details

- **Server:** `nginx/1.26.2`, `x-handler: census`
- **CORS:** `Access-Control-Allow-Origin: *`
- **Unknown query params:** silently ignored
- **Pagination:** `meta.next` / `meta.prev` are relative query strings;
  null when there are no more pages
- **Total corpus is small enough to bulk-download** in tens of thousands
  of requests — but please cache, don't re-fetch

---

## See also

- [`1901_1911_CENSUS.md`](./1901_1911_CENSUS.md) — same host, different
  endpoint (`/census/query`), covering the full surviving 1901 and 1911
  censuses (~8.8M rows)
- [`1926_CENSUS.md`](./1926_CENSUS.md) — the post-independence 1926
  census on a separate host (`c26-api.nationalarchives.ie`)
