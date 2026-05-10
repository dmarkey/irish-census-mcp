"""End-to-end walk-through of the driving use case from README.md.

    "My great-grandfather was Patrick Murphy from Skibbereen, Co. Cork.
     His wife was Mary O'Brien, whose mother Catherine was from Strabane,
     Co. Tyrone. Find me possible relatives."

This isn't a strict pass/fail test — it prints a trace of what an LLM
client would see by chaining the tools. Run with:

    uv run pytest tests/test_walkthrough.py -s
"""

from __future__ import annotations

import json

import pytest

from irish_census_mcp import places
from irish_census_mcp.gateway import CensusGateway


def _show(label: str, obj) -> None:
    print(f"\n=== {label} ===")
    print(json.dumps(obj, indent=2, default=str)[:2500])


@pytest.fixture
async def gw():
    g = CensusGateway()
    try:
        yield g
    finally:
        await g.aclose()


async def test_walk_through(gw):
    # 1. Resolve the two places mentioned in the user query
    skibbereen = [p.as_dict() for p in places.resolve("Skibbereen Co Cork")]
    strabane = [p.as_dict() for p in places.resolve("Strabane Co Tyrone")]
    _show("resolve_place: Skibbereen Cork", skibbereen)
    _show("resolve_place: Strabane Tyrone", strabane)
    assert 1926 not in strabane[0]["available_in"], \
        "Tyrone should be excluded from 1926"

    # 2. Search for Patrick Murphy in Cork (Skibbereen area)
    patricks = await gw.search_people(
        surname="Murphy",
        first_name="Patrick",
        county="Cork",
        place="Skibbereen",
        year="all",
        limit=10,
    )
    _show("search_people: Patrick Murphy Skibbereen Cork", patricks)

    # 3. If we found candidates, reconstruct one household
    if patricks["results"]:
        first = patricks["results"][0]
        if first.get("household_key"):
            household = await gw.get_household(first["household_key"])
            _show("get_household: Patrick's family", household)
            marys = [
                m for m in household["members"]
                if (m.get("first_name") or "").lower().startswith("mary")
            ]
            print(f"\nMarys in Patrick's household: {len(marys)}")

    # 4. Search for Mary O'Brien (any county)
    marys = await gw.search_people(
        surname="O'Brien",
        first_name="Mary",
        year="all",
        limit=10,
    )
    _show("search_people: Mary O'Brien (any county)", marys)
    print(f"\nTotal O'Brien candidates: {len(marys['results'])}")

    # 5. Search for Catherine in Strabane, Tyrone (1901/1911 only)
    catherines = await gw.search_people(
        surname="O'Brien",
        first_name="Catherine",
        county="Tyrone",
        place="Strabane",
        year="all",
        limit=10,
    )
    _show("search_people: Catherine O'Brien Strabane Tyrone", catherines)
    skipped = catherines["meta"].get("skipped_corpora", [])
    assert any(s.get("year") == 1926 for s in skipped), \
        "Tyrone search should explicitly skip the 1926 corpus"
