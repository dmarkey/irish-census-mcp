"""FastMCP server exposing the Irish census APIs to LLM clients."""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastmcp import FastMCP
from pydantic import Field

from . import places
from .gateway import CensusGateway

# Global gateway, created on first use. FastMCP supports lifespans, but a lazy
# global keeps this importable without a running event loop.
_gateway: CensusGateway | None = None


def _get_gateway() -> CensusGateway:
    global _gateway
    if _gateway is None:
        _gateway = CensusGateway()
    return _gateway


@asynccontextmanager
async def lifespan(_app):
    yield
    if _gateway is not None:
        await _gateway.aclose()


mcp = FastMCP(
    name="irish-census",
    instructions=(
        "Search the Irish National Archives 1821-1926 census records and "
        "the Irish Genealogy BMD (births, marriages, deaths, baptisms, "
        "burials) records.\n\n"
        "Census workflow: `resolve_place` to identify which censuses cover "
        "a place, then `search_people` to find candidates, then "
        "`get_household` to reconstruct families. Census refs have the form "
        "`<year>:<id>` (e.g. '1911:3666567').\n\n"
        "BMD workflow: `bmd_search` to find vital-record candidates, then "
        "`bmd_get_record` for the full transcription and `bmd_get_image_url` "
        "for the scan PDF. BMD refs have the form `bmd:<record_id>` "
        "(e.g. 'bmd:cima-2914616'). Use `bmd_search_relatives` to bridge "
        "from a census person to their birth/marriage/death records."
    ),
    lifespan=lifespan,
)


@mcp.tool
async def resolve_place(
    query: Annotated[str, Field(description="Free-text place, e.g. 'Skibbereen Co Cork' or 'Strabane Tyrone'.")]
) -> list[dict]:
    """Resolve a free-text place name to a canonical (county, sub_place) tuple plus year coverage.

    Returns one or more candidates, each indicating which census years cover that
    county. Northern Ireland counties (Antrim, Armagh, Down, Fermanagh,
    Londonderry, Tyrone) are NOT in the 1926 census — the `available_in` field
    will reflect this. Use the returned `county` (canonical form) and
    `sub_place` directly with `search_people`.
    """
    return [c.as_dict() for c in places.resolve(query)]


@mcp.tool
async def search_people(
    surname: Annotated[str | None, Field(description="Surname, fuzzy by default.")] = None,
    first_name: Annotated[str | None, Field(description="First name, fuzzy by default.")] = None,
    year: Annotated[
        int | Literal["all"],
        Field(description="One of 1821, 1831, 1841, 1851, 1901, 1911, 1926, or 'all' (default)."),
    ] = "all",
    county: Annotated[str | None, Field(description="Canonical modern county name, e.g. 'Monaghan'.")] = None,
    place: Annotated[
        str | None,
        Field(description="Free-text place; resolved to county + sub_place automatically if `county` is not given. Otherwise treated as a townland/DED/parish hint."),
    ] = None,
    age: Annotated[int | None, Field(description="Approximate age (matched ±2 years).")] = None,
    age_range: Annotated[tuple[int, int] | None, Field(description="Inclusive age range, e.g. [40, 50].")] = None,
    sex: Annotated[Literal["M", "F"] | None, Field(description="M or F.")] = None,
    religion: Annotated[str | None, Field(description="Normalized religion string, e.g. 'Roman Catholic'.")] = None,
    fuzzy: Annotated[bool, Field(description="When True (default), uses substring/case-insensitive name matching.")] = True,
    detail: Annotated[
        Literal["brief", "full"],
        Field(description="'brief' returns just ref/name/age/place/year/household_key/seen_in. 'full' (default) adds sex, marriage, religion, occupation, relation."),
    ] = "full",
    limit: Annotated[int, Field(description="Max rows to return (default 20, max 100).", ge=1, le=100)] = 20,
    page: Annotated[int, Field(description="Zero-based page index.", ge=0)] = 0,
) -> dict:
    """Search people across all three Irish census APIs (1821-1926).

    Returns compact rows plus metadata. When `year='all'`, results from
    different censuses that plausibly represent the same person are merged
    with a `seen_in` field and `related_refs` pointing at the other matches.
    `related_refs` is capped at 3 — a `related_refs_truncated` count
    indicates additional matches.

    Use the returned `household_key` with `get_household` to reconstruct
    families; use `ref` with `get_person` for full details. Set
    `detail='brief'` when scanning many candidates to minimise tokens.
    """
    gw = _get_gateway()
    return await gw.search_people(
        surname=surname,
        first_name=first_name,
        year=year,
        county=county,
        place=place,
        age=age,
        age_range=age_range,
        sex=sex,
        religion=religion,
        fuzzy=fuzzy,
        detail=detail,
        limit=limit,
        page=page,
    )


@mcp.tool
async def get_household(
    household_key: Annotated[str, Field(description="Household key from search_people, e.g. '1911:796666'.")]
) -> dict:
    """Return everyone enumerated together on the same household form.

    For 1926 and 1901/1911, members are grouped by `image_group`. For
    pre-Famine fragments, grouping is best-effort by `first_image`.

    Response includes scan URLs (Form A / B / N) you can pass to the user as
    citation links.
    """
    gw = _get_gateway()
    return await gw.get_household(household_key)


@mcp.tool
async def get_person(
    ref: Annotated[str, Field(description="Person ref from search_people, e.g. '1911:3666567'.")],
    include_raw: Annotated[
        bool,
        Field(description="When True, includes the underlying API row (null-stripped). Off by default to save tokens."),
    ] = False,
) -> dict:
    """Return the full normalized record for one person.

    By default returns only the normalized fields (~20-30 tokens of overhead).
    Pass `include_raw=True` to also get the underlying API row when you need
    fields not exposed by the normalized projection (e.g. children_born,
    education, deafdumb for 1911; folio_num for c19).
    """
    gw = _get_gateway()
    return await gw.get_person(ref, include_raw=include_raw)


@mcp.tool
async def find_relatives(
    ref: Annotated[str, Field(description="Subject person's ref, e.g. '1926:1407409'.")],
    spread: Annotated[
        int,
        Field(description="0 = household only, 1 = +same person in adjacent censuses, 2 = +parent-household candidates.", ge=0, le=2),
    ] = 1,
) -> dict:
    """Bundle the common 'find relatives' chains around one person.

    - spread=0: just the household (equivalent to get_household).
    - spread=1: + the same person as found in adjacent census years.
    - spread=2: + best-guess parent-household candidates with confidence scores.

    Returns a tree-shaped response to minimise token usage. `parent_household_candidates`
    are heuristic — scores reflect surname match, county match, and plausible
    parental age band. The LLM should treat them as candidates to verify, not
    assertions.
    """
    gw = _get_gateway()
    return await gw.find_relatives(ref, spread=spread)


@mcp.tool
async def get_scan_url(
    ref: Annotated[str, Field(description="Person ref, e.g. '1911:3666567'.")],
    form: Annotated[
        Literal["A", "B", "B1", "B2", "N"],
        Field(description="Form letter. A = individual return. B = household/building (1926). B1/B2/N = 1901/1911 specific."),
    ] = "A",
) -> dict:
    """Return URL(s) for the scan of the requested form.

    The URLs are stable API endpoints — fetching one returns a 307 redirect to
    a signed Linode storage URL valid for 30 minutes. Pass the API URL to the
    user as a citation; don't cache the redirected target.
    """
    gw = _get_gateway()
    return await gw.get_scan_url(ref, form=form)


@mcp.tool
async def bmd_search(
    surname: Annotated[str | None, Field(description="Surname / last name. Fuzzy unless `exact=True`.")] = None,
    first_name: Annotated[str | None, Field(description="First name / forename.")] = None,
    mothers_surname: Annotated[
        str | None,
        Field(description="Mother's birth surname (only meaningful for birth records)."),
    ] = None,
    events: Annotated[
        list[Literal["birth", "marriage", "death", "baptism", "burial"]] | None,
        Field(description="Event types to include. Defaults to all five if omitted."),
    ] = None,
    year_start: Annotated[int | None, Field(description="Inclusive lower bound on event year.")] = None,
    year_end: Annotated[int | None, Field(description="Inclusive upper bound on event year.")] = None,
    location: Annotated[
        str | None,
        Field(description="Free-text location filter (county, town, registration district). Server-side fuzzy."),
    ] = None,
    source: Annotated[
        Literal["all", "civil", "church"],
        Field(description="Restrict to civil (GRO) records, church records, or both."),
    ] = "all",
    exact: Annotated[bool, Field(description="When True, names match exactly rather than fuzzily.")] = False,
    sort: Annotated[
        Literal["relevance", "date"],
        Field(description="Result ordering. 'relevance' is the site default."),
    ] = "relevance",
    page: Annotated[int, Field(description="1-based page number.", ge=1)] = 1,
    per_page: Annotated[
        Literal[10, 20, 50, 100],
        Field(description="Results per page (server supports 10/20/50/100)."),
    ] = 20,
    age_at_death: Annotated[int | None, Field(description="Death-record filter (deceased's age).")] = None,
) -> dict:
    """Search Irish Genealogy BMD (births, marriages, deaths, baptisms, burials).

    Covers civil registration (births from 1864, deaths from 1864, civil
    marriages from 1845) and a growing set of church registers (RC, CoI,
    Presbyterian, etc.) digitised by irishgenealogy.ie.

    Returns at most `per_page` rows plus metadata (`total` or
    `total_capped=True` if >10,000). Results include `ref` (`bmd:<id>`),
    event type, source ('civil'|'church'), ISO date when parseable,
    party names, and per-record meta (district, parish, mother's surname,
    etc.). Pass `ref` to `bmd_get_record` for the full transcription.

    Tip: leave events unset to widen the net; pin `year_start`/`year_end`
    when a surname returns 10,000+ results.
    """
    gw = _get_gateway()
    return await gw.bmd_search(
        surname=surname,
        first_name=first_name,
        mothers_surname=mothers_surname,
        events=events,
        year_start=year_start,
        year_end=year_end,
        location=location,
        source=source,
        exact=exact,
        sort=sort,
        page=page,
        per_page=per_page,
        age_at_death=age_at_death,
    )


@mcp.tool
async def bmd_get_record(
    ref: Annotated[str, Field(description="BMD ref from bmd_search, e.g. 'bmd:cima-2914616'.")],
) -> dict:
    """Return the full transcription of one BMD record.

    Field set varies by record type:
    - Civil marriage: party names, date, group reg ID, district, image path.
    - Civil birth: name, date, mother's birth surname, sex, district.
    - Civil death: name, date, district, deceased's age.
    - Church records: name, address, parents (births/marriages), priest,
      witnesses (marriages), sponsors (baptisms), book/page/entry numbers.

    `image_url` is the direct PDF link to the scan when available (some
    older church records lack scans). Use `bmd_get_image_url` if you only
    need the link.
    """
    gw = _get_gateway()
    return await gw.bmd_get_record(ref)


@mcp.tool
async def bmd_get_image_url(
    ref: Annotated[str, Field(description="BMD ref, e.g. 'bmd:cima-2914616'.")],
) -> dict:
    """Return the scan PDF URL for a BMD record, plus event/source.

    Convenience helper — equivalent to `bmd_get_record(ref).image_url` but
    returns a slimmer payload suitable for citation flows. `url` may be
    null when no scan exists.
    """
    gw = _get_gateway()
    return await gw.bmd_get_image_url(ref)


@mcp.tool
async def bmd_search_relatives(
    census_ref: Annotated[
        str | None,
        Field(description="Census ref (e.g. '1911:3666567'). When given, surname/first_name/birth_year/location are derived from the census record."),
    ] = None,
    surname: Annotated[str | None, Field(description="Required if `census_ref` is not provided.")] = None,
    first_name: Annotated[str | None, Field(description="Subject's first name.")] = None,
    mothers_surname: Annotated[str | None, Field(description="Mother's birth surname (helps narrow birth candidates).")] = None,
    birth_year: Annotated[int | None, Field(description="Estimated birth year. Derived from census ref if available.")] = None,
    location: Annotated[str | None, Field(description="County/district hint passed to the BMD search.")] = None,
    events: Annotated[
        list[Literal["birth", "marriage", "death", "baptism", "burial"]] | None,
        Field(description="Event types to chase. Defaults to ['birth','marriage','death']."),
    ] = None,
) -> dict:
    """Heuristically surface BMD events for a known person.

    Runs targeted bmd_search queries:
    - birth/baptism within ±3 years of estimated birth year (using
      `mothers_surname` if provided)
    - marriage within ages 16-45 of estimated birth year
    - death/burial after the subject's last seen census year

    Returns candidate lists (max 10 per event type). These are heuristic —
    treat each as a candidate to verify with `bmd_get_record`, not a
    confirmed match.

    Civil registration only goes back to 1864 (1845 for non-Catholic
    marriages); church records cover earlier dates spottily.
    """
    gw = _get_gateway()
    return await gw.bmd_search_relatives(
        census_ref=census_ref,
        surname=surname,
        first_name=first_name,
        mothers_surname=mothers_surname,
        birth_year=birth_year,
        location=location,
        events=events,
    )


def run() -> None:
    """Entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    run()
