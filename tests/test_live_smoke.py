"""End-to-end smoke test against the live National Archives APIs.

Run with: `uv run pytest tests/test_live_smoke.py -s`

These tests hit the real APIs. They're idempotent reads, no auth needed.
Skip them in CI behind an environment guard if desired.
"""

from __future__ import annotations

import os

import pytest

from irish_census_mcp.gateway import CensusGateway
from irish_census_mcp import places

LIVE = os.environ.get("SKIP_LIVE") != "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="SKIP_LIVE=1 set")


@pytest.fixture
async def gw():
    g = CensusGateway()
    try:
        yield g
    finally:
        await g.aclose()


async def test_resolve_place_ni_excludes_1926():
    out = places.resolve("Strabane Co Tyrone")
    assert out, "should resolve"
    top = out[0]
    assert top.county == "Tyrone"
    assert 1926 not in top.available_in
    assert 1901 in top.available_in and 1911 in top.available_in


async def test_resolve_place_skibbereen_cork():
    out = places.resolve("Skibbereen Co Cork")
    assert out
    top = out[0]
    assert top.county == "Cork"
    assert top.sub_place and "Skibbereen" in top.sub_place
    assert set(top.available_in) >= {1901, 1911, 1926}


async def test_resolve_alias_offaly():
    out = places.resolve("Offaly")
    assert out and out[0].county == "Offaly"


async def test_search_murphy_cork(gw):
    resp = await gw.search_people(
        surname="Murphy",
        county="Cork",
        year=1911,
        limit=10,
    )
    assert resp["results"], "should find Murphys in Cork 1911"
    for row in resp["results"]:
        assert row["year"] == 1911
        assert row["surname"].lower().startswith("murph")
        assert row["ref"].startswith("1911:")
        assert "household_key" in row


async def test_household_reconstruction_1911(gw):
    # Find a Murphy in a populated household
    resp = await gw.search_people(
        surname="Murphy", county="Cork", year=1911, limit=20,
    )
    for row in resp["results"]:
        hh = await gw.get_household(row["household_key"])
        if len(hh["members"]) > 1:
            assert hh["year"] == 1911
            assert hh["scans"], "1911 households should include scan URLs"
            break
    else:
        pytest.fail("No multi-person Murphy household found in 1911 Cork")


async def test_search_year_all_dedups(gw):
    resp = await gw.search_people(
        surname="Murphy",
        first_name="Denis",
        county="Cork",
        year="all",
        limit=20,
    )
    refs = [r["ref"] for r in resp["results"]]
    assert len(refs) == len(set(refs)), "refs should be unique"
    # At least one row should have seen_in populated (dedup worked)
    has_dedup = any("seen_in" in r and len(r["seen_in"]) > 1 for r in resp["results"])
    print("dedup hit:", has_dedup, "rows:", len(resp["results"]))


async def test_get_person_1911(gw):
    resp = await gw.search_people(surname="Murphy", county="Cork", year=1911, limit=1)
    ref = resp["results"][0]["ref"]
    person = await gw.get_person(ref)
    assert person["ref"] == ref
    assert person["year"] == 1911
    assert "raw" not in person, "raw should be opt-in"
    # Opt in
    person_full = await gw.get_person(ref, include_raw=True)
    assert "raw" in person_full


async def test_get_scan_url_1911(gw):
    resp = await gw.search_people(surname="Murphy", county="Cork", year=1911, limit=1)
    ref = resp["results"][0]["ref"]
    scan = await gw.get_scan_url(ref, form="A")
    assert scan["urls"]
    assert all(u.endswith(".pdf") for u in scan["urls"])


async def test_find_relatives_spread_1(gw):
    resp = await gw.search_people(surname="Murphy", county="Cork", year=1911, limit=1)
    ref = resp["results"][0]["ref"]
    tree = await gw.find_relatives(ref, spread=1)
    assert tree["subject"]["ref"] == ref
    assert "household_now" in tree
    assert "earlier_self" in tree


async def test_ni_county_search_skips_1926(gw):
    """Northern Ireland counties were not part of the Free State, so the
    1926 corpus must be skipped when searching them."""
    resp = await gw.search_people(
        surname="Murphy",
        county="Tyrone",
        year="all",
        limit=10,
    )
    skipped = resp["meta"]["skipped_corpora"]
    assert any(s["year"] == 1926 for s in skipped), \
        f"1926 should be skipped for Tyrone; got {skipped}"
