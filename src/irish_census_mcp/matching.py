"""Cross-census person dedup and parent-household scoring."""

from __future__ import annotations

from rapidfuzz import fuzz

# Census years in order — used to compute age deltas
YEAR_ORDER = [1821, 1831, 1841, 1851, 1901, 1911, 1926]


def _initial(s: str | None) -> str:
    return (s or "")[:1].lower()


def _surname_key(s: str | None) -> str:
    if not s:
        return ""
    # Lowercase, strip common Mac/Mc/O' variations to a common form
    s = s.lower().replace("'", "")
    if s.startswith("mc "):
        s = "mc" + s[3:]
    elif s.startswith("mac "):
        s = "mac" + s[4:]
    return s


def _county_from_place(place: str | None) -> str:
    """The county is the last comma-separated segment."""
    if not place:
        return ""
    return place.split(",")[-1].strip().lower()


def _likely_same(a: dict, b: dict, age_tol: int = 3) -> bool:
    """Decide if two normalized rows likely represent the same physical person."""
    if _surname_key(a.get("surname")) != _surname_key(b.get("surname")):
        # Tolerate close surname spellings
        if fuzz.ratio(a.get("surname") or "", b.get("surname") or "") < 88:
            return False
    if _initial(a.get("first_name")) != _initial(b.get("first_name")):
        return False
    # Sex must agree where known
    if a.get("sex") and b.get("sex") and a["sex"] != b["sex"]:
        return False
    # County must agree
    if _county_from_place(a.get("place")) != _county_from_place(b.get("place")):
        return False
    # Age delta consistent with year delta?
    age_a, age_b = a.get("age"), b.get("age")
    year_a, year_b = a.get("year"), b.get("year")
    if age_a is not None and age_b is not None and year_a and year_b:
        expected = year_b - year_a
        actual = age_b - age_a
        if abs(expected - actual) > age_tol:
            return False
    # First name fuzzy match
    if fuzz.ratio(a.get("first_name") or "", b.get("first_name") or "") < 70:
        return False
    return True


MAX_RELATED_REFS = 3


def dedup_across_years(rows: list[dict]) -> list[dict]:
    """Merge plausibly-same-person rows from different census years.

    Each row keeps its own ref but gains a ``seen_in`` list of years where it
    likely also appears, plus up to ``MAX_RELATED_REFS`` ``related_refs``
    pointing at the other matches so the LLM can drill into any of them.
    Truncation is signalled by ``related_refs_truncated``.
    """
    # Group by surname-key for efficiency
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(_surname_key(r.get("surname")), []).append(r)

    out: list[dict] = []
    for group in groups.values():
        # Sort newest first so the canonical row is the most recent appearance
        group.sort(key=lambda r: (r.get("year") or 0), reverse=True)
        merged: list[list[dict]] = []
        for r in group:
            placed = False
            for bucket in merged:
                if any(_likely_same(r, x) for x in bucket):
                    bucket.append(r)
                    placed = True
                    break
            if not placed:
                merged.append([r])

        for bucket in merged:
            if len(bucket) == 1:
                out.append(bucket[0])
                continue
            primary = bucket[0]
            others = bucket[1:]
            primary = dict(primary)
            primary["seen_in"] = sorted({r["year"] for r in bucket})
            primary["related_refs"] = [r["ref"] for r in others[:MAX_RELATED_REFS]]
            if len(others) > MAX_RELATED_REFS:
                primary["related_refs_truncated"] = len(others) - MAX_RELATED_REFS
            out.append(primary)

    # Stable sort by year desc, surname asc
    out.sort(key=lambda r: (-(r.get("year") or 0), r.get("surname") or "", r.get("first_name") or ""))
    return out


def score_parent_candidate(child: dict, head: dict, year_at_birth_min: int = 14) -> float:
    """Heuristic score that ``head`` is plausibly a parent of ``child``.

    Both rows are normalized dicts from different census years."""
    if not (child.get("year") and head.get("year") and child.get("age") is not None and head.get("age") is not None):
        return 0.0
    # Year of birth (approx)
    child_dob = child["year"] - child["age"]
    head_dob = head["year"] - head["age"]
    delta = child_dob - head_dob
    # Plausible parental age range
    if delta < year_at_birth_min or delta > 55:
        return 0.0
    # County match required
    if _county_from_place(child.get("place")) != _county_from_place(head.get("place")):
        return 0.0
    # Surname match (parent should share child's surname most of the time —
    # not always for daughters, but workable as heuristic)
    surname_match = fuzz.ratio(child.get("surname") or "", head.get("surname") or "") / 100.0
    if surname_match < 0.7:
        return 0.0
    age_band_score = 1.0 - abs(delta - 30) / 40.0
    return max(0.0, min(1.0, 0.6 * surname_match + 0.4 * age_band_score))
