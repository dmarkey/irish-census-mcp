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
        "Search and reconstruct families from the Irish National Archives "
        "1821-1926 census records. Start with `resolve_place` to identify "
        "which censuses cover a place, then `search_people` to find "
        "candidates, then `get_household` to reconstruct families. Refs "
        "have the form `<year>:<id>` (e.g. '1911:3666567')."
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


def run() -> None:
    """Entry point: run the server over stdio."""
    mcp.run()


if __name__ == "__main__":
    run()
