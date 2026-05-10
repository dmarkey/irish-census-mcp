"""Result normalization across the three census schemas.

Each corpus uses different field names; this module collapses them into a
single compact dict shape:

    {
        "ref": "<year>:<id>",
        "name": "First Last",
        "first_name": "First",
        "surname": "Last",
        "age": int | None,
        "sex": "M"|"F"|None,
        "place": "Townland, DED/Parish, County",
        "relation": str | None,
        "marriage": str | None,
        "religion": str | None,
        "occupation": str | None,
        "household_key": "<year>:<group>" | None,
        "year": int,
    }
"""

from __future__ import annotations

from typing import Any


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    if isinstance(v, int):
        return v
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _place_str(*parts: str | None) -> str:
    return ", ".join(p for p in parts if p)


def normalize_1926(row: dict) -> dict:
    a_id = row["a_id"]
    return {
        "ref": f"1926:{a_id}",
        "name": " ".join(p for p in (row.get("first_name"), row.get("surname")) if p),
        "first_name": row.get("first_name"),
        "surname": row.get("surname"),
        "age": _safe_int(row.get("updated_age")),
        "sex": row.get("updated_sex"),
        "place": _place_str(row.get("townland"), row.get("ded"), row.get("county")),
        "relation": row.get("updated_relationship_to_head") or row.get("relationship_to_head"),
        "marriage": row.get("updated_marriage"),
        "religion": row.get("updated_religion"),
        "occupation": None,  # not exposed in c26a query response
        "household_key": f"1926:{row['image_group']}" if row.get("image_group") else None,
        "year": 1926,
        "_raw_image_group": row.get("image_group"),
        "_raw_aform": row.get("aform_name"),
    }


def normalize_1911(row: dict) -> dict:
    rid = row["id"]
    return {
        "ref": f"{row['census_year']}:{rid}",
        "name": " ".join(p for p in (row.get("firstname"), row.get("surname")) if p),
        "first_name": row.get("firstname"),
        "surname": row.get("surname"),
        "age": _safe_int(row.get("age")),
        "sex": row.get("sex"),
        "place": _place_str(row.get("townland"), row.get("ded"), row.get("county")),
        "relation": row.get("relation_to_head_updated") or row.get("relation_to_head"),
        "marriage": row.get("marriage_status"),
        "religion": row.get("religion_updated") or row.get("religion"),
        "occupation": row.get("occupation_updated") or row.get("occupation"),
        "household_key": (
            f"{row['census_year']}:{row['image_group']}" if row.get("image_group") else None
        ),
        "year": row["census_year"],
        "_raw_image_group": row.get("image_group"),
        "_raw_images": row.get("images") or [],
    }


def normalize_c19(row: dict) -> dict:
    rid = row["id"]
    return {
        "ref": f"{row['census_year']}:c19-{rid}",
        "name": " ".join(p for p in (row.get("firstname"), row.get("surname")) if p),
        "first_name": row.get("firstname"),
        "surname": row.get("surname"),
        "age": _safe_int(row.get("age")),
        "sex": row.get("sex"),
        "place": _place_str(
            row.get("townland"), row.get("parish"), row.get("barony"), row.get("county")
        ),
        "relation": row.get("relation_to_head"),
        "marriage": row.get("marriage_status") or row.get("marital_status"),
        "religion": None,  # household counts only — not per person
        "occupation": row.get("occupation"),
        "household_key": (
            # Best-effort grouping: same folio + townland + year
            f"{row['census_year']}:c19-{row.get('first_image')}"
            if row.get("first_image")
            else None
        ),
        "year": row["census_year"],
        "_raw_first_image": row.get("first_image"),
        "_raw_folio_num": row.get("folio_num"),
        "_raw_hoh_flag": row.get("hoh_flag"),
    }


def strip_nulls(d: dict) -> dict:
    """Drop None/empty values to keep tool responses compact."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}


def strip_internals(d: dict) -> dict:
    """Drop leading-underscore keys before returning to the LLM."""
    return {k: v for k, v in d.items() if not k.startswith("_")}


def public(d: dict) -> dict:
    return strip_nulls(strip_internals(d))


# Field sets for the `detail` parameter on search_people
BRIEF_FIELDS = {
    "ref", "name", "age", "place", "year",
    "household_key", "seen_in", "related_refs", "related_refs_truncated",
}


def brief(d: dict) -> dict:
    """Ultra-compact projection: only the fields useful for triage / drill-down."""
    return {k: v for k, v in d.items() if k in BRIEF_FIELDS and v not in (None, "", [], {})}


def deep_strip_nulls(d: dict) -> dict:
    """Recursive null-stripping for nested raw API payloads."""
    if isinstance(d, dict):
        return {k: deep_strip_nulls(v) for k, v in d.items() if v not in (None, "", [], {})}
    if isinstance(d, list):
        return [deep_strip_nulls(x) for x in d if x not in (None, "", [], {})]
    return d
