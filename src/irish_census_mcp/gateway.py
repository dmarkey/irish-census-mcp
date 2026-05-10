"""High-level orchestrator: parses refs, fans out to clients, normalizes results."""

from __future__ import annotations

import asyncio
from typing import Any

from . import matching, normalize, places
from .api import Census1926, Census19011911, Census19th, CensusAPIError, CensusHTTP
from .bmd import IrishGenealogyBMD, parse_bmd_ref

ALL_YEARS = (1821, 1831, 1841, 1851, 1901, 1911, 1926)
C19_YEARS = (1821, 1831, 1841, 1851)
C19XX_YEARS = (1901, 1911)

# Caps on response sizes to keep tool payloads context-friendly
MAX_HOUSEHOLD_MEMBERS = 30
MAX_EARLIER_SELF = 3
MAX_PARENT_CANDIDATES = 5


class Ref:
    """Parsed opaque reference of the form `<year>:<id>` or `<year>:c19-<id>`."""

    __slots__ = ("year", "is_c19", "id_str")

    def __init__(self, year: int, is_c19: bool, id_str: str) -> None:
        self.year = year
        self.is_c19 = is_c19
        self.id_str = id_str

    @classmethod
    def parse(cls, ref: str) -> "Ref":
        try:
            year_str, rest = ref.split(":", 1)
            year = int(year_str)
        except (ValueError, AttributeError):
            raise ValueError(f"Bad ref: {ref!r}. Expected '<year>:<id>'.")
        is_c19 = rest.startswith("c19-")
        if is_c19:
            rest = rest[len("c19-") :]
        return cls(year=year, is_c19=is_c19, id_str=rest)

    @property
    def id_int(self) -> int:
        return int(self.id_str)


def _parse_household_key(key: str) -> tuple[int, bool, str]:
    """Returns (year, is_c19, group_id_str)."""
    year_str, rest = key.split(":", 1)
    year = int(year_str)
    is_c19 = rest.startswith("c19-")
    if is_c19:
        rest = rest[len("c19-") :]
    return year, is_c19, rest


class CensusGateway:
    """One per process. Owns the shared HTTP client and per-corpus clients."""

    def __init__(self) -> None:
        self.http = CensusHTTP()
        self.c26 = Census1926(self.http)
        self.c1911 = Census19011911(self.http)
        self.c19 = Census19th(self.http)
        self.bmd = IrishGenealogyBMD(self.http)

    async def aclose(self) -> None:
        await self.http.aclose()

    # ------------------------------------------------------------------
    # search_people
    # ------------------------------------------------------------------

    async def search_people(
        self,
        *,
        surname: str | None = None,
        first_name: str | None = None,
        year: int | str = "all",
        county: str | None = None,
        place: str | None = None,
        age: int | None = None,
        age_range: tuple[int, int] | None = None,
        sex: str | None = None,
        religion: str | None = None,
        fuzzy: bool = True,
        detail: str = "full",
        limit: int = 20,
        page: int = 0,
    ) -> dict:
        # Resolve place if given as free-text (no county supplied)
        sub_place: str | None = None
        county_canonical = county
        if place and not county:
            candidates = places.resolve(place)
            if candidates:
                top = candidates[0]
                county_canonical = top.county
                sub_place = top.sub_place
        elif place and county:
            sub_place = place

        # Determine target years
        if year == "all" or year is None:
            target_years = list(ALL_YEARS)
        else:
            target_years = [int(year)]

        # Filter by county availability
        skipped: list[dict] = []
        if county_canonical:
            keep: list[int] = []
            for y in target_years:
                mapped = places.county_for_corpus(county_canonical, y)
                if mapped is None:
                    skipped.append(
                        {
                            "year": y,
                            "reason": f"{county_canonical} not covered by {y} census",
                        }
                    )
                else:
                    keep.append(y)
            target_years = keep

        # Compute age bounds
        age_min: int | None = None
        age_max: int | None = None
        if age is not None:
            age_min = age - 2
            age_max = age + 2
        elif age_range is not None:
            age_min, age_max = age_range

        # Each corpus call gets a wider limit so post-dedup we still have results
        per_corpus_limit = min(max(limit * 2, 30), 100)
        offset = page * limit

        # sub_place could be a townland, DED, or parish — the API only supports
        # AND-style filtering, so when sub_place is set we fan out to multiple
        # queries per corpus (one per location-field interpretation) and union.
        place_variants: list[str] = ["townland", "ded"] if sub_place else [""]

        tasks: list[tuple[int, asyncio.Task]] = []

        if 1926 in target_years:
            c26_county = places.county_for_corpus(county_canonical, 1926) if county_canonical else None
            for variant in place_variants:
                tasks.append(
                    (
                        1926,
                        asyncio.create_task(
                            self.c26.query(
                                surname=surname,
                                first_name=first_name,
                                county=c26_county,
                                townland=sub_place if variant == "townland" else None,
                                ded=sub_place if variant == "ded" else None,
                                sex=sex,
                                religion=religion,
                                age_min=age_min,
                                age_max=age_max,
                                fuzzy=fuzzy,
                                limit=per_corpus_limit,
                                offset=offset,
                            )
                        ),
                    )
                )

        years_19xx = [y for y in target_years if y in C19XX_YEARS]
        for y in years_19xx:
            c1911_county = places.county_for_corpus(county_canonical, y) if county_canonical else None
            for variant in place_variants:
                tasks.append(
                    (
                        y,
                        asyncio.create_task(
                            self.c1911.query(
                                surname=surname,
                                firstname=first_name,
                                census_year=y,
                                county=c1911_county,
                                townland=sub_place if variant == "townland" else None,
                                ded=sub_place if variant == "ded" else None,
                                sex=sex,
                                religion_updated=religion,
                                age_min=age_min,
                                age_max=age_max,
                                fuzzy=fuzzy,
                                limit=per_corpus_limit,
                                offset=offset,
                            )
                        ),
                    )
                )

        # Pre-Famine corpus uses parish, not DED
        c19_variants: list[str] = ["townland", "parish"] if sub_place else [""]
        years_c19 = [y for y in target_years if y in C19_YEARS]
        for y in years_c19:
            c19_county = places.county_for_corpus(county_canonical, y) if county_canonical else None
            for variant in c19_variants:
                tasks.append(
                    (
                        y,
                        asyncio.create_task(
                            self.c19.query(
                                surname=surname,
                                firstname=first_name,
                                census_year=y,
                                county=c19_county,
                                townland=sub_place if variant == "townland" else None,
                                parish=sub_place if variant == "parish" else None,
                                fuzzy=fuzzy,
                                limit=per_corpus_limit,
                                offset=offset,
                            )
                        ),
                    )
                )

        # Gather, collecting per-corpus errors as notes rather than failing all
        notes: list[str] = []
        all_rows: list[dict] = []
        seen_refs: set[str] = set()
        totals: dict[int, int] = {}
        for y, task in tasks:
            try:
                resp = await task
            except CensusAPIError as e:
                notes.append(f"{y}: {e}")
                continue
            # When fan-out produces multiple per-year tasks (townland + ded
            # variants), keep the max count we observe — they're overlapping
            # views of the same year.
            totals[y] = max(totals.get(y, 0), resp["meta"]["count"])
            for raw in resp.get("results", []):
                if y == 1926:
                    row = normalize.normalize_1926(raw)
                elif y in C19XX_YEARS:
                    row = normalize.normalize_1911(raw)
                else:
                    row = normalize.normalize_c19(raw)
                if row["ref"] in seen_refs:
                    continue
                seen_refs.add(row["ref"])
                all_rows.append(row)

        # Dedup across years only when asking for "all"
        if year == "all" or year is None:
            merged = matching.dedup_across_years(all_rows)
        else:
            merged = all_rows

        # Paginate (post-dedup)
        sliced = merged[: limit]
        more = len(merged) > limit or any(
            totals.get(y, 0) > offset + per_corpus_limit for y in target_years
        )

        # Project rows according to detail level
        if detail == "brief":
            projected = [normalize.brief(r) for r in sliced]
        else:
            projected = [normalize.public(r) for r in sliced]

        # Build a slim meta — drop empty/zero entries
        meta: dict[str, Any] = {
            "page": page,
            "more_available": bool(more),
        }
        nonzero_totals = {str(y): n for y, n in totals.items() if n}
        if nonzero_totals:
            meta["total_per_corpus"] = nonzero_totals
        if skipped:
            meta["skipped_corpora"] = skipped
        if notes:
            meta["notes"] = notes
        if county_canonical:
            meta["resolved_county"] = county_canonical
        if sub_place:
            meta["resolved_sub_place"] = sub_place

        return {"results": projected, "meta": meta}

    # ------------------------------------------------------------------
    # get_household
    # ------------------------------------------------------------------

    async def get_household(self, household_key: str) -> dict:
        year, is_c19, group_id = _parse_household_key(household_key)
        members: list[dict]
        scans: dict[str, list[str]] = {}
        place_str: str | None = None
        house_number: str | None = None

        if year == 1926:
            resp = await self.c26.query(image_group=int(group_id), limit=50)
            rows = [normalize.normalize_1926(r) for r in resp["results"]]
            members = rows
            if rows:
                place_str = rows[0].get("place")
            # Look up scans
            try:
                related = await self.c26.related_images(int(group_id))
                scans["form_a"] = [self.c26.image_url(n) for n in related.get("aform_names", [])]
                scans["form_b"] = [self.c26.image_url(n) for n in related.get("bform_names", [])]
            except CensusAPIError:
                pass
        elif year in C19XX_YEARS:
            resp = await self.c1911.query(image_group=group_id, census_year=year, limit=50)
            rows = [normalize.normalize_1911(r) for r in resp["results"]]
            members = rows
            if rows:
                place_str = rows[0].get("place")
                house_number = resp["results"][0].get("house_number")
                # Use the first member's embedded images list
                imgs = rows[0].get("_raw_images", [])
                by_form: dict[str, list[str]] = {}
                for img in imgs:
                    form = (img.get("form") or "").replace(" ", "_").lower()
                    if not form:
                        continue
                    url = self.c1911.image_url(img["id"])
                    by_form.setdefault(form, []).append(url)
                scans = by_form
        else:
            # Pre-Famine — group_id is the first_image of the originating row
            resp = await self.c19.query(census_year=year, limit=50)
            # Best-effort: filter rows that share the first_image
            rows = [normalize.normalize_c19(r) for r in resp["results"]]
            members = [r for r in rows if r.get("_raw_first_image") == group_id]
            if not members and rows:
                members = rows[:1]
            if members:
                place_str = members[0].get("place")
            if group_id:
                scans["folio"] = [self.c19.image_url(group_id)]

        truncated_members = 0
        if len(members) > MAX_HOUSEHOLD_MEMBERS:
            truncated_members = len(members) - MAX_HOUSEHOLD_MEMBERS
            members = members[:MAX_HOUSEHOLD_MEMBERS]

        result: dict[str, Any] = {
            "household_key": household_key,
            "year": year,
            "place": place_str,
            "members": [normalize.public(m) for m in members],
        }
        if house_number:
            result["house_number"] = house_number
        if truncated_members:
            result["members_truncated"] = truncated_members
            result["members_total"] = truncated_members + MAX_HOUSEHOLD_MEMBERS
        if scans:
            result["scans"] = scans
        return result

    # ------------------------------------------------------------------
    # get_person
    # ------------------------------------------------------------------

    async def get_person(self, ref: str, include_raw: bool = False) -> dict:
        r = Ref.parse(ref)
        if r.year == 1926 and not r.is_c19:
            resp = await self.c26.query(a_id=r.id_int, limit=1)
            if not resp["results"]:
                raise ValueError(f"No 1926 person with ref {ref}")
            raw = resp["results"][0]
            n = normalize.public(normalize.normalize_1926(raw))
        elif r.year in C19XX_YEARS and not r.is_c19:
            resp = await self.c1911.query(id_=r.id_int, census_year=r.year, limit=1)
            if not resp["results"]:
                raise ValueError(f"No {r.year} person with ref {ref}")
            raw = resp["results"][0]
            n = normalize.public(normalize.normalize_1911(raw))
        elif r.is_c19:
            resp = await self.c19.query(id_=r.id_int, census_year=r.year, limit=1)
            if not resp["results"]:
                raise ValueError(f"No c19 person with ref {ref}")
            raw = resp["results"][0]
            n = normalize.public(normalize.normalize_c19(raw))
        else:
            raise ValueError(f"Unsupported ref: {ref}")

        if include_raw:
            # Deep-strip nulls so the raw payload doesn't pad the response
            n = {**n, "raw": normalize.deep_strip_nulls(raw)}
        return n

    # ------------------------------------------------------------------
    # find_relatives
    # ------------------------------------------------------------------

    async def find_relatives(self, ref: str, spread: int = 1) -> dict:
        r = Ref.parse(ref)
        person = await self.get_person(ref)
        household_key = person.get("household_key")
        result: dict[str, Any] = {"subject": {k: person.get(k) for k in ("ref", "name", "age", "sex", "place", "year")}}
        if household_key:
            result["household_now"] = await self.get_household(household_key)

        if spread >= 1:
            # Look for the same person in adjacent census years
            adjacent: list[int] = []
            if r.year == 1926:
                adjacent = [1911, 1901]
            elif r.year == 1911:
                adjacent = [1926, 1901]
            elif r.year == 1901:
                adjacent = [1911, 1926]
            elif r.year in C19_YEARS:
                # Cross-corpus matching for pre-Famine is unreliable; skip.
                adjacent = []
            earlier_self: list[dict] = []
            for y in adjacent:
                age = person.get("age")
                age_at_y = age - (r.year - y) if isinstance(age, int) else None
                search = await self.search_people(
                    surname=person.get("surname"),
                    first_name=person.get("first_name"),
                    year=y,
                    county=_county_from_place_str(person.get("place")),
                    age=age_at_y,
                    fuzzy=True,
                    detail="brief",
                    limit=5,
                )
                for cand in search["results"][:2]:
                    earlier_self.append(cand)
                if len(earlier_self) >= MAX_EARLIER_SELF:
                    break
            result["earlier_self"] = earlier_self[:MAX_EARLIER_SELF]

        if spread >= 2:
            # Parent-household candidates: for the subject's youngest census
            # appearance, look at earlier censuses for households whose head's
            # surname matches and whose age is plausible.
            target_years = []
            if r.year == 1926:
                target_years = [1911, 1901]
            elif r.year == 1911:
                target_years = [1901]
            else:
                target_years = []
            parent_candidates: list[dict] = []
            for y in target_years:
                # Head of household, same surname, same county, of age that
                # puts them as plausible parent of the subject's birth year.
                search = await self.search_people(
                    surname=person.get("surname"),
                    year=y,
                    county=_county_from_place_str(person.get("place")),
                    fuzzy=True,
                    limit=20,
                )
                for cand in search["results"]:
                    if cand.get("relation") and "head" in cand["relation"].lower():
                        score = matching.score_parent_candidate(person, cand)
                        if score > 0.5:
                            parent_candidates.append(
                                {
                                    "household_key": cand.get("household_key"),
                                    "head": {k: cand.get(k) for k in ("ref", "name", "age", "place")},
                                    "match_score": round(score, 2),
                                }
                            )
            parent_candidates.sort(key=lambda c: -c["match_score"])
            result["parent_household_candidates"] = parent_candidates[:MAX_PARENT_CANDIDATES]

        return result

    # ------------------------------------------------------------------
    # get_scan_url
    # ------------------------------------------------------------------

    async def get_scan_url(self, ref: str, form: str = "A") -> dict:
        r = Ref.parse(ref)
        if r.year == 1926 and not r.is_c19:
            resp = await self.c26.query(a_id=r.id_int, limit=1)
            if not resp["results"]:
                raise ValueError(f"No 1926 person with ref {ref}")
            row = resp["results"][0]
            aform = row.get("aform_name")
            if form.upper() == "A" and aform:
                return {
                    "url": self.c26.image_url(aform),
                    "form": "Form A",
                    "year": 1926,
                    "note": "Append other forms via get_household().scans for Form B.",
                }
            related = await self.c26.related_images(row["image_group"])
            wanted = "bform_names" if form.upper() == "B" else "aform_names"
            urls = [self.c26.image_url(n) for n in related.get(wanted, [])]
            return {"urls": urls, "form": f"Form {form.upper()}", "year": 1926}
        if r.year in C19XX_YEARS:
            resp = await self.c1911.query(id_=r.id_int, census_year=r.year, limit=1)
            if not resp["results"]:
                raise ValueError(f"No {r.year} person with ref {ref}")
            imgs = resp["results"][0].get("images", []) or []
            want = ("form " + form.lower()).strip()
            matching_imgs = [i for i in imgs if i.get("form", "").lower() == want]
            if not matching_imgs and imgs:
                matching_imgs = [i for i in imgs if i.get("form", "").lower().startswith("form a")]
            return {
                "urls": [self.c1911.image_url(i["id"]) for i in matching_imgs],
                "form": matching_imgs[0]["form"] if matching_imgs else f"Form {form.upper()}",
                "year": r.year,
                "note": "Each URL 307s to a Linode signed URL valid for 30 minutes.",
            }
        if r.is_c19:
            resp = await self.c19.query(id_=r.id_int, census_year=r.year, limit=1)
            if not resp["results"]:
                raise ValueError(f"No c19 person with ref {ref}")
            first_image = resp["results"][0].get("first_image")
            if not first_image:
                return {"urls": [], "year": r.year, "note": "No image associated."}
            return {
                "urls": [self.c19.image_url(first_image)],
                "form": "Folio",
                "year": r.year,
            }
        raise ValueError(f"Unsupported ref: {ref}")

    # ------------------------------------------------------------------
    # BMD (irishgenealogy.ie) — births, marriages, deaths, baptisms, burials
    # ------------------------------------------------------------------

    async def bmd_search(
        self,
        *,
        surname: str | None = None,
        first_name: str | None = None,
        mothers_surname: str | None = None,
        events: list[str] | None = None,
        year_start: int | None = None,
        year_end: int | None = None,
        location: str | None = None,
        source: str = "all",
        exact: bool = False,
        sort: str = "relevance",
        page: int = 1,
        per_page: int = 20,
        age_at_death: int | None = None,
    ) -> dict[str, Any]:
        resp = await self.bmd.search(
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
        meta: dict[str, Any] = {
            "page": resp["page"],
            "per_page": resp["per_page"],
            "count_text": resp["count_text"],
        }
        if resp["count"] is not None:
            meta["total"] = resp["count"]
        else:
            # Capped at 10000+ — tell the LLM to narrow the search.
            meta["total_capped"] = True
        if resp["last_page"]:
            meta["last_page"] = resp["last_page"]
        if resp["centuries"]:
            meta["centuries"] = resp["centuries"]
        return {"results": resp["results"], "meta": meta}

    async def bmd_get_record(self, ref: str) -> dict[str, Any]:
        return await self.bmd.get_record(parse_bmd_ref(ref))

    async def bmd_get_image_url(self, ref: str) -> dict[str, Any]:
        d = await self.bmd.get_record(parse_bmd_ref(ref))
        return {
            "ref": ref,
            "url": d.get("image_url"),
            "event": d.get("event"),
            "source": d.get("source"),
        }

    async def bmd_search_relatives(
        self,
        *,
        census_ref: str | None = None,
        surname: str | None = None,
        first_name: str | None = None,
        mothers_surname: str | None = None,
        birth_year: int | None = None,
        location: str | None = None,
        events: list[str] | None = None,
    ) -> dict[str, Any]:
        subject: dict[str, Any] = {}
        if census_ref:
            person = await self.get_person(census_ref)
            subject = {
                "ref": person.get("ref"),
                "name": person.get("name"),
                "year": person.get("year"),
                "age": person.get("age"),
                "place": person.get("place"),
            }
            surname = surname or person.get("surname")
            first_name = first_name or person.get("first_name")
            age = person.get("age")
            if isinstance(age, int) and isinstance(person.get("year"), int):
                birth_year = birth_year or (person["year"] - age)
            if not location:
                location = _county_from_place_str(person.get("place"))

        if not surname:
            raise ValueError("Need either census_ref or surname")

        valid = {"birth", "marriage", "death", "baptism", "burial"}
        requested = [e for e in (events or ["birth", "marriage", "death"]) if e in valid]
        if not requested:
            raise ValueError("No valid event types requested")

        out: dict[str, Any] = {}
        if subject:
            out["subject"] = subject

        async def _run(**kw: Any) -> list[dict[str, Any]]:
            try:
                resp = await self.bmd.search(
                    surname=surname,
                    first_name=first_name,
                    location=location,
                    per_page=_BMD_PER_EVENT,
                    **kw,
                )
                return resp["results"][:_BMD_PER_EVENT]
            except CensusAPIError:
                return []

        tasks: dict[str, asyncio.Task] = {}
        if "birth" in requested and birth_year:
            tasks["birth_candidates"] = asyncio.create_task(
                _run(
                    events=["birth", "baptism"],
                    year_start=birth_year - _BMD_BIRTH_WINDOW,
                    year_end=birth_year + _BMD_BIRTH_WINDOW,
                    mothers_surname=mothers_surname,
                )
            )
        if "marriage" in requested and birth_year:
            tasks["marriage_candidates"] = asyncio.create_task(
                _run(
                    events=["marriage"],
                    year_start=birth_year + _BMD_MARRIAGE_MIN_AGE,
                    year_end=birth_year + _BMD_MARRIAGE_MAX_AGE,
                )
            )
        if "death" in requested:
            ys: int | None = None
            ye: int | None = None
            if birth_year:
                ys = birth_year + 1
                ye = birth_year + 100
            if subject and subject.get("year"):
                ys = max(ys or 1864, int(subject["year"]))
            tasks["death_candidates"] = asyncio.create_task(
                _run(events=["death", "burial"], year_start=ys, year_end=ye)
            )

        for k, t in tasks.items():
            out[k] = await t
        out["notes"] = (
            "Candidates only. Civil registration started in 1864 "
            "(non-Catholic marriages from 1845). Use bmd_get_record to "
            "verify any candidate."
        )
        return out


def _county_from_place_str(place: str | None) -> str | None:
    if not place:
        return None
    parts = [p.strip() for p in place.split(",")]
    return parts[-1] if parts else None


# BMD search-relatives heuristics
_BMD_BIRTH_WINDOW = 3        # ± years around estimated birth year
_BMD_MARRIAGE_MIN_AGE = 16
_BMD_MARRIAGE_MAX_AGE = 45
_BMD_PER_EVENT = 10
