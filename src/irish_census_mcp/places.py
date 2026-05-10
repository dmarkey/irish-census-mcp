"""Place resolution: free-text -> canonical (county, sub-place, year-coverage)."""

from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz, process

# All 32 historic counties of Ireland (1901/1911 coverage).
# Spelling used in the API matches these forms exactly.
COUNTIES_32 = [
    "Antrim", "Armagh", "Carlow", "Cavan", "Clare", "Cork", "Donegal",
    "Down", "Dublin", "Fermanagh", "Galway", "Kerry", "Kildare", "Kilkenny",
    "Kings", "Leitrim", "Limerick", "Londonderry", "Longford", "Louth",
    "Mayo", "Meath", "Monaghan", "Queens", "Roscommon", "Sligo",
    "Tipperary", "Tyrone", "Waterford", "Westmeath", "Wexford", "Wicklow",
]

# 26 counties of the Free State (1926 coverage). Modern names — the 1926
# census already used "Laois"/"Offaly" rather than "Queens"/"Kings".
COUNTIES_26 = [
    "Carlow", "Cavan", "Clare", "Cork", "Donegal", "Dublin", "Galway",
    "Kerry", "Kildare", "Kilkenny", "Laois", "Leitrim", "Limerick",
    "Longford", "Louth", "Mayo", "Meath", "Monaghan", "Offaly",
    "Roscommon", "Sligo", "Tipperary", "Waterford", "Westmeath",
    "Wexford", "Wicklow",
]

NORTHERN_COUNTIES = {"Antrim", "Armagh", "Down", "Fermanagh", "Londonderry", "Tyrone"}

# Modern <-> 1901/1911 name aliases.
COUNTY_ALIASES = {
    "laois": "Queens",
    "queen's": "Queens",
    "queens county": "Queens",
    "offaly": "Kings",
    "king's": "Kings",
    "kings county": "Kings",
    "derry": "Londonderry",
    "co derry": "Londonderry",
    "county derry": "Londonderry",
}


@dataclass
class ResolvedPlace:
    county: str | None
    sub_place: str | None  # townland / parish / DED / street — passed through
    raw_query: str
    available_in: list[int]
    confidence: float
    notes: str | None = None

    def as_dict(self) -> dict:
        d = {
            "county": self.county,
            "sub_place": self.sub_place,
            "available_in": self.available_in,
            "confidence": round(self.confidence, 2),
        }
        if self.notes:
            d["notes"] = self.notes
        return d


def _normalize(s: str) -> str:
    return s.strip().lower().replace(".", "").replace(",", " ")


def _strip_prefixes(s: str) -> str:
    # "Co", "Co.", "County" are connectors before a county name — drop them
    # but KEEP the county name that follows.
    return " ".join(t for t in s.split() if t not in ("co", "county"))


def _county_for_1911(name: str) -> str:
    """Map a canonical modern county name to its 1901/1911 form."""
    if name == "Laois":
        return "Queens"
    if name == "Offaly":
        return "Kings"
    return name


def _county_for_1926(name: str) -> str | None:
    """Map a canonical name to its 1926 form, or None if outside the Free State."""
    if name in ("Queens",):
        return "Laois"
    if name in ("Kings",):
        return "Offaly"
    if name in NORTHERN_COUNTIES:
        return None
    return name if name in COUNTIES_26 else None


def _available_years(canonical_county: str) -> list[int]:
    """Years a county can appear in across all three APIs."""
    years = [1821, 1831, 1841, 1851, 1901, 1911]  # all 32 counties potentially
    if canonical_county not in NORTHERN_COUNTIES:
        years.append(1926)
    return years


def resolve(query: str) -> list[ResolvedPlace]:
    """Resolve free-text like 'Skibbereen Co Cork' into structured candidates.

    Strategy:
      1. Strip "Co", "County" connectors.
      2. Test every token (and pairs) against the 32-county list with fuzzy match.
      3. The remainder becomes sub_place, passed through as a townland/DED/parish
         hint to the search tools.
    """
    if not query or not query.strip():
        return []

    norm = _strip_prefixes(_normalize(query))
    tokens = norm.split()
    if not tokens:
        return []

    # Try aliases first
    full_norm = norm
    if full_norm in COUNTY_ALIASES:
        canonical_modern = {
            "Queens": "Laois",
            "Kings": "Offaly",
        }.get(COUNTY_ALIASES[full_norm], COUNTY_ALIASES[full_norm])
        return [
            ResolvedPlace(
                county=canonical_modern,
                sub_place=None,
                raw_query=query,
                available_in=_available_years(COUNTY_ALIASES[full_norm]),
                confidence=1.0,
            )
        ]

    # Find best county match across the 32-county list, biased to longer matches.
    # `processor=str.lower` makes comparison case-insensitive — important
    # because WRatio is otherwise case-sensitive, which costs short county
    # names like "Cork" enough to fall below the 80-point cutoff.
    best_county = None
    best_score = 0.0
    best_span = (0, 0)
    for i in range(len(tokens)):
        for j in range(i + 1, min(i + 4, len(tokens) + 1)):
            chunk = " ".join(tokens[i:j])
            match = process.extractOne(
                chunk, COUNTIES_32,
                scorer=fuzz.WRatio,
                processor=str.lower,
                score_cutoff=80,
            )
            if match and match[1] > best_score:
                best_county, best_score, _ = match
                best_span = (i, j)

        # Also test alias forms
        for i_end in range(i + 1, min(i + 4, len(tokens) + 1)):
            chunk = " ".join(tokens[i:i_end])
            if chunk in COUNTY_ALIASES:
                alias_target = COUNTY_ALIASES[chunk]
                if best_score < 100:
                    best_county = alias_target
                    best_score = 100.0
                    best_span = (i, i_end)

    if not best_county:
        # No county found — return a low-confidence pass-through that the
        # search tool can still try as a townland.
        return [
            ResolvedPlace(
                county=None,
                sub_place=query.strip(),
                raw_query=query,
                available_in=[1821, 1831, 1841, 1851, 1901, 1911, 1926],
                confidence=0.3,
                notes="No county recognised; sub_place will be matched fuzzily.",
            )
        ]

    # sub_place is whatever wasn't claimed by the county match
    sub_tokens = tokens[: best_span[0]] + tokens[best_span[1] :]
    sub_place = " ".join(sub_tokens).strip() or None
    if sub_place:
        sub_place = sub_place.title()

    # Normalize county to modern name for display
    display_county = best_county
    if best_county == "Queens":
        display_county = "Laois"
    elif best_county == "Kings":
        display_county = "Offaly"

    return [
        ResolvedPlace(
            county=display_county,
            sub_place=sub_place,
            raw_query=query,
            available_in=_available_years(best_county),
            confidence=min(best_score / 100.0, 1.0),
        )
    ]


def county_for_corpus(canonical_modern: str, year: int) -> str | None:
    """Convert a canonical modern county name to the form a given corpus uses."""
    if year == 1926:
        return _county_for_1926(canonical_modern)
    # Pre-1926 corpora (1901/1911 + c19) use the old names
    return _county_for_1911(canonical_modern)
