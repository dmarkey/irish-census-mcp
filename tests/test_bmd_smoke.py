"""End-to-end smoke test for the BMD (Irish Genealogy) tools.

Hits the live irishgenealogy.ie site. These are idempotent reads, no auth.
Skip with `SKIP_LIVE=1`.
"""

from __future__ import annotations

import os

import pytest

from irish_census_mcp.bmd import (
    classify_source,
    parse_bmd_ref,
    parse_detail_html,
    parse_search_html,
    _parse_date,
)
from irish_census_mcp.gateway import CensusGateway

LIVE = os.environ.get("SKIP_LIVE") != "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="SKIP_LIVE=1 set")


@pytest.fixture
async def gw():
    g = CensusGateway()
    try:
        yield g
    finally:
        await g.aclose()


# ---------------------------------------------------------------------------
# Pure parser tests (no network) — left unguarded so SKIP_LIVE=1 still exercises
# them.
# ---------------------------------------------------------------------------


def test_parse_bmd_ref_round_trip():
    assert parse_bmd_ref("bmd:cima-1689162") == "cima-1689162"
    assert parse_bmd_ref("bmd:6c90ec090e-159") == "6c90ec090e-159"
    with pytest.raises(ValueError):
        parse_bmd_ref("1911:3666567")


def test_classify_source_prefixes():
    assert classify_source("cima-1") == "civil"
    assert classify_source("cide-1") == "civil"
    assert classify_source("cidenf-1") == "civil"
    assert classify_source("e768beed6b-1") == "civil"
    # Unknown hex prefix defaults to church when no context given
    assert classify_source("6c90ec090e-1") == "church"


def test_parse_date_forms():
    assert _parse_date("07 November 1882") == "1882-11-07"
    assert _parse_date("05/11/1919") == "1919-11-05"
    assert _parse_date("1881") == "1881"
    assert _parse_date("nonsense") is None


def test_parse_search_no_results():
    parsed = parse_search_html("<p>No results found. Please try a different search.</p>")
    assert parsed["count"] == 0
    assert parsed["results"] == []


def test_parse_search_synthetic_record():
    html = (
        '<li><a href="/view?record_id=cima-1689162">'
        '<h5>Marriage of <strong>PATRICK MARKEY</strong> and '
        '<strong>JULIA MARKEY</strong> on <strong>05 November 1919</strong>'
        '<span class="civil-church-label">&nbsp; ᐧ &nbsp;Civil record</span></h5>'
        '<p><div><strong>Group registration ID: </strong>1689162</div>'
        '<div><strong>SR District/Reg Area: </strong>Navan</div></p>'
        '</a></li>'
        '<p>1408 results found</p>'
    )
    parsed = parse_search_html(html)
    assert parsed["count"] == 1408
    assert len(parsed["results"]) == 1
    r = parsed["results"][0]
    assert r["ref"] == "bmd:cima-1689162"
    assert r["event"] == "marriage"
    assert r["source"] == "civil"
    assert r["date"] == "1919-11-05"
    assert r["parties"] == ["PATRICK MARKEY", "JULIA MARKEY"]
    assert r["meta"]["SR District/Reg Area"] == "Navan"


# ---------------------------------------------------------------------------
# Live tests (skip with SKIP_LIVE=1)
# ---------------------------------------------------------------------------


async def test_bmd_search_bounded(gw):
    s = await gw.bmd_search(
        surname="Markey",
        first_name="Patrick",
        year_start=1918,
        year_end=1922,
        per_page=10,
    )
    assert s["results"], "expected at least one Patrick Markey result"
    assert isinstance(s["meta"]["count_text"], str)
    # Every result has the canonical shape
    for r in s["results"]:
        assert r["ref"].startswith("bmd:")
        assert r["event"] in {"birth", "marriage", "death", "baptism", "burial"}
        assert r["source"] in {"civil", "church"}
        assert r["parties"]


async def test_bmd_get_record_then_image(gw):
    s = await gw.bmd_search(
        surname="Markey",
        first_name="Patrick",
        year_start=1918,
        year_end=1922,
        per_page=5,
    )
    # Find a civil marriage that should always have an image
    civil_marriages = [r for r in s["results"] if r["event"] == "marriage" and r["source"] == "civil"]
    assert civil_marriages, "expected at least one civil marriage in the test slice"
    ref = civil_marriages[0]["ref"]

    rec = await gw.bmd_get_record(ref)
    assert rec["ref"] == ref
    assert rec["event"] == "marriage"
    assert rec["source"] == "civil"
    assert rec["fields"], "civil marriage should have transcribed fields"
    assert rec["image_url"], "civil marriage should always have a scan URL"
    assert rec["image_url"].endswith(".pdf")

    img = await gw.bmd_get_image_url(ref)
    assert img["url"] == rec["image_url"]
    assert img["event"] == "marriage"


async def test_bmd_search_capped_returns_total_capped(gw):
    # Plain surname=Markey hits the 10000+ cap
    s = await gw.bmd_search(surname="Markey", per_page=10)
    assert s["meta"].get("total_capped") is True
    assert "total" not in s["meta"]


async def test_bmd_get_record_parses_church(gw):
    # 6c90ec090e-159 is a stable church-marriage record we vetted manually.
    rec = await gw.bmd_get_record("bmd:6c90ec090e-159")
    assert rec["event"] == "marriage"
    assert rec["source"] == "church"
    # Two-party rows produce list values
    assert isinstance(rec["fields"].get("Name"), list)
    assert len(rec["fields"]["Name"]) == 2
    assert "Witness 1" in rec["fields"]
