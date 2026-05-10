"""Irish Genealogy (irishgenealogy.ie) BMD client and parsers.

This site exposes Irish births, marriages, deaths, baptisms, and burials from
civil registers (GRO) and church records. There's no JSON API — we parse the
server-rendered HTML for both the results list and the per-record detail
pages.

Record IDs in this corpus are alphanumeric (e.g. 'cima-1689162',
'e768beed6b-242319'). They are stable identifiers that route directly into
the detail endpoint at `/view/?record_id=<id>`. The known prefix grammar:

  cima-      civil marriage
  cide-      civil death (named)
  cidenf-    civil death (no first name)
  <hex>-     civil birth (modern collection) OR church record

Church records use a hex prefix per source collection so their type cannot
be inferred from the prefix alone — we read it from the result's HTML
header instead.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any, Iterable
from urllib.parse import urlencode

from .api import CensusAPIError, CensusHTTP

BMD_BASE = "https://www.irishgenealogy.ie"
BMD_HOST = "www.irishgenealogy.ie"

EVENT_TYPES = ("birth", "marriage", "death", "baptism", "burial")
SOURCE_VALUES = ("all", "civil", "church")
SORT_VALUES = ("relevance", "date")

# Civil prefixes are semantic; church record IDs are hex digests per
# collection and have to be classified via the result header text instead.
_CIVIL_PREFIXES = {"cima", "cide", "cidenf"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _strip_tags(s: str) -> str:
    return _WS_RE.sub(" ", _html.unescape(_TAG_RE.sub(" ", s))).strip()


_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


def _parse_date(raw: str) -> str | None:
    """Return ISO-8601 date if parseable, else None. Handles forms like
    '07 November 1882', '07/11/1882', '1881', '21 February 1922'."""
    if not raw:
        return None
    s = raw.strip()
    # DD/MM/YYYY
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        d, mo, y = m.groups()
        return f"{int(y):04d}-{int(mo):02d}-{int(d):02d}"
    # DD Month YYYY
    m = re.fullmatch(r"(\d{1,2})\s+([A-Za-z]+)\s+(\d{4})", s)
    if m:
        d, mo_name, y = m.groups()
        mo = _MONTHS.get(mo_name.lower())
        if mo:
            return f"{int(y):04d}-{mo:02d}-{int(d):02d}"
    # Year only
    if re.fullmatch(r"\d{4}", s):
        return s
    return None


def parse_bmd_ref(ref: str) -> str:
    """Extract the record_id from a 'bmd:<record_id>' ref."""
    if not ref.startswith("bmd:"):
        raise ValueError(f"Bad BMD ref: {ref!r}. Expected 'bmd:<record_id>'.")
    return ref[len("bmd:"):]


def classify_source(record_id: str, context: str = "") -> str:
    """Return 'civil' or 'church'.

    Pass `context` as: the per-result `<h5>` HTML when parsing search results
    (it carries a 'Civil record' / 'Church record' label span), or the full
    detail HTML when parsing a /view page (we then key off the
    /files/{civil,church}/ image path). Detail pages also embed the search
    form, which mentions both strings — so the broad substring match is only
    safe on the narrower search-result snippet.
    """
    prefix = record_id.split("-", 1)[0]
    if prefix in _CIVIL_PREFIXES:
        return "civil"
    if "/files/civil/" in context:
        return "civil"
    if "/files/church/" in context:
        return "church"
    if "Civil record" in context:
        return "civil"
    if "Church record" in context:
        return "church"
    # Civil-birth uses a stable hex prefix.
    if prefix == "e768beed6b":
        return "civil"
    return "church"


# ---------------------------------------------------------------------------
# Search results parsing
# ---------------------------------------------------------------------------


# A search result is one <li> wrapping an anchor to /view?record_id=ID,
# containing an <h5> with the event type/name/date and a <p> with metadata
# divs.
_RESULT_RE = re.compile(
    r'<li><a href="/view\?record_id=(?P<rid>[^"]+)">\s*<h5[^>]*>(?P<header>.*?)</h5>\s*<p>(?P<body>.*?)</p>\s*</a>\s*</li>',
    re.S,
)

# Each metadata div is `<div><strong>Label: </strong>Value</div>`.
_META_DIV_RE = re.compile(
    r"<div>\s*<strong>(?P<label>[^<]+?):?\s*</strong>\s*(?P<value>.*?)\s*</div>",
    re.S,
)

_COUNT_RE = re.compile(r'(?P<count>(?:\d[\d,]*\+?|No))\s+results?\s+found', re.I)
_CENTURY_RE = re.compile(
    r"filterByCentury\('(\d{4})'\)[^>]*>\s*\d{4}\s*\((\d+)\)"
)
_PAGINATION_LAST_RE = re.compile(r"pageTo\((\d+)\)\s*\"[^>]*>\s*(\d+)\s*</a>")


def _parse_event_from_header(header_clean: str) -> tuple[str | None, list[str], str | None]:
    """Pull event type, party names, and date from cleaned header text.

    Header forms encountered:
      'Marriage of X and Y on 07 November 1882 ᐧ Church record'
      'Marriage of X and Y on 05/11/1919 ᐧ Civil record'
      'Birth of X on 21 February 1922 ᐧ Civil record'
      'Death of X in 1881 ᐧ Civil record'
      'Baptism of X of <ADDRESS> in 1901 ᐧ Church record'
      'Burial of X on 22 December 1806 ᐧ Church record'
    """
    text = header_clean
    event: str | None = None
    for ev in EVENT_TYPES:
        if text.lower().startswith(ev + " of "):
            event = ev
            text = text[len(ev) + 4:]
            break
    parties: list[str] = []
    date: str | None = None
    # Strip the trailing record-source label
    text = re.sub(r"\s*ᐧ\s*(Civil|Church)\s+record\b.*$", "", text).strip()
    # Try ' on <date>' (Birth/Marriage/Burial)
    m = re.search(r"\s+on\s+(.+)$", text)
    if m:
        date = m.group(1).strip()
        text = text[: m.start()].strip()
    else:
        # ' in <year>' (Death/Baptism)
        m = re.search(r"\s+in\s+(\d{4}|[^,]+)$", text)
        if m:
            date = m.group(1).strip()
            text = text[: m.start()].strip()
    # Marriages have " and " in the remaining name block
    if event == "marriage" and " and " in text:
        left, right = text.split(" and ", 1)
        parties = [left.strip(), right.strip()]
    else:
        # Baptisms include 'of <ADDRESS>' tail; drop it
        text = re.sub(r"\s+of\s+.+$", "", text)
        if text:
            parties = [text.strip()]
    return event, parties, date


def parse_search_html(html: str) -> dict[str, Any]:
    """Parse a search-results page into:

        {
          "results": [ {ref, record_id, event, source, date, date_raw, parties, meta}, ... ],
          "count": int | None,         # None for 10000+ cap
          "count_text": str,           # original count string
          "centuries": {1900: 8680, ...},  # if present
          "last_page": int | None,
        }
    """
    out_results: list[dict[str, Any]] = []
    for m in _RESULT_RE.finditer(html):
        rid = m.group("rid")
        header_raw = m.group("header")
        body_raw = m.group("body")
        header_clean = _strip_tags(header_raw)
        event, parties, date_raw = _parse_event_from_header(header_clean)
        source = classify_source(rid, header_raw)
        meta: dict[str, str] = {}
        for d in _META_DIV_RE.finditer(body_raw):
            label = _strip_tags(d.group("label"))
            value = _strip_tags(d.group("value"))
            if label:
                meta[label] = value
        item: dict[str, Any] = {
            "ref": f"bmd:{rid}",
            "record_id": rid,
            "event": event,
            "source": source,
            "date_raw": date_raw,
            "date": _parse_date(date_raw) if date_raw else None,
            "parties": parties,
        }
        if meta:
            item["meta"] = meta
        out_results.append(item)

    count_text = ""
    count: int | None = None
    cm = _COUNT_RE.search(html)
    if cm:
        count_text = cm.group(0).strip()
        raw = cm.group("count").replace(",", "")
        if raw.lower() == "no":
            count = 0
        elif raw.endswith("+"):
            count = None  # capped at 10000+, exact total unknown
        else:
            try:
                count = int(raw)
            except ValueError:
                count = None

    centuries: dict[int, int] = {}
    for cy in _CENTURY_RE.finditer(html):
        centuries[int(cy.group(1))] = int(cy.group(2))

    last_page: int | None = None
    # The last entry in the pagination block is the highest page number link;
    # if absent (e.g. single-page result), there is no further pagination.
    pages = [int(g) for g in re.findall(r"pageTo\((\d+)\)", html)]
    if pages:
        last_page = max(pages)

    return {
        "results": out_results,
        "count": count,
        "count_text": count_text,
        "centuries": centuries,
        "last_page": last_page,
    }


# ---------------------------------------------------------------------------
# Detail-page parsing
# ---------------------------------------------------------------------------


_H3_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.S)
_TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
_TD_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S)
_PDF_HREF_RE = re.compile(r'href="(/files/[^"]+\.pdf)"')


def parse_detail_html(record_id: str, html: str) -> dict[str, Any]:
    """Parse the /view detail page into a normalized dict.

    The page renders one `<table class="table">` with rows shaped as either:
      - 2-cell: label / value
      - 3-cell: label / party1 / party2 (church marriage)
      - 1-cell section header (e.g. 'Further details in the record')
      - 1-cell 'View record image' link row (we skip; image url is taken
        from the <a href>)

    Fields are case-preserved per the source so callers can spot exact labels
    when comparing across record types.
    """
    h3 = _H3_RE.search(html)
    header = _strip_tags(h3.group(1)) if h3 else ""
    pdf_match = _PDF_HREF_RE.search(html)
    pdf_href = pdf_match.group(1) if pdf_match else ""
    # Detail pages embed the search form, which mentions both 'Civil record'
    # and 'Church record' literals, so we must not pass the full HTML to the
    # classifier. The image path is the authoritative signal; when missing,
    # we rely on the record-id prefix (church records use opaque hex
    # prefixes that all default to 'church').
    source = classify_source(record_id, pdf_href)

    fields: dict[str, Any] = {}
    sections: list[str] = []
    current_section: str | None = None

    for tr in _TR_RE.finditer(html):
        tds = [_strip_tags(t) for t in _TD_RE.findall(tr.group(1))]
        if not tds:
            continue
        # Section headers carry colspan=2 or colspan=3 and exactly one cell
        if len(tds) == 1:
            label = tds[0].rstrip(":")
            if label.lower() in {"view record image", ""}:
                continue
            current_section = label
            sections.append(label)
            continue
        # 2-cell: label / value
        if len(tds) == 2:
            label = tds[0].rstrip(":").strip()
            value = tds[1]
            if not label:
                continue
            # Skip the "View record image" row that sometimes has two cells
            if "view record image" in label.lower():
                continue
            fields[label] = value
            continue
        # 3-cell: label / party1 / party2 (church marriage)
        if len(tds) == 3:
            label = tds[0].rstrip(":").strip()
            if not label:
                continue
            fields[label] = [tds[1], tds[2]]

    image_url: str | None = None
    pm = _PDF_HREF_RE.search(html)
    if pm:
        image_url = f"{BMD_BASE}{pm.group(1)}"

    # Pull the event type out of the header ('Marriage record for ...').
    event = None
    m = re.match(r"\s*(\w+)\s+record\b", header, re.I)
    if m:
        ev = m.group(1).lower()
        if ev in EVENT_TYPES:
            event = ev

    return {
        "ref": f"bmd:{record_id}",
        "record_id": record_id,
        "event": event,
        "source": source,
        "header": header,
        "fields": fields,
        "sections": sections,
        "image_url": image_url,
    }


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class IrishGenealogyBMD:
    """Thin client for irishgenealogy.ie BMD records."""

    def __init__(self, http: CensusHTTP) -> None:
        self.http = http

    async def _get_html(self, path: str, params: dict[str, Any]) -> str:
        # The site is HTML, not JSON — bypass CensusHTTP.get's json parsing.
        url = f"{BMD_BASE}{path}"
        async with self.http._sem_for(BMD_HOST):  # noqa: SLF001
            r = await self.http._client.get(  # noqa: SLF001
                url,
                params={k: v for k, v in params.items() if v is not None and v != ""},
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": f"{BMD_BASE}/",
                },
            )
        if r.status_code >= 400:
            raise CensusAPIError(f"{r.status_code} from {url}: {r.text[:200]}")
        return r.text

    async def search(
        self,
        *,
        surname: str | None = None,
        first_name: str | None = None,
        mothers_surname: str | None = None,
        events: Iterable[str] | None = None,
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
        if source not in SOURCE_VALUES:
            raise ValueError(f"Invalid source: {source!r}")
        if sort not in SORT_VALUES:
            raise ValueError(f"Invalid sort: {sort!r}")
        # The site treats unspecified event filters as "none selected" and
        # returns no results, so default to all five.
        evs = list(events) if events else list(EVENT_TYPES)
        bad = [e for e in evs if e not in EVENT_TYPES]
        if bad:
            raise ValueError(f"Unknown event(s): {bad}")
        params: dict[str, Any] = {
            "church-or-civil": source,
            "lastname": surname or "",
            "firstname": first_name or "",
            "mothers-surname": mothers_surname or "",
            "location": location or "",
            "yearStart": year_start if year_start is not None else "",
            "yearEnd": year_end if year_end is not None else "",
            "age-at-death": age_at_death if age_at_death is not None else "",
            "pg": page,
            "per_page": per_page,
            "sortby": "date" if sort == "date" else None,
        }
        if exact:
            params["exact-matches-only"] = 1
        for ev in evs:
            params[f"event-{ev}"] = 1
        html = await self._get_html("/search/", params)
        parsed = parse_search_html(html)
        parsed["page"] = page
        parsed["per_page"] = per_page
        return parsed

    async def get_record(self, record_id: str) -> dict[str, Any]:
        html = await self._get_html("/view/", {"record_id": record_id})
        if "No record found" in html or "<h3" not in html:
            raise CensusAPIError(f"No record: {record_id}")
        return parse_detail_html(record_id, html)

    def search_url(
        self,
        **kwargs: Any,
    ) -> str:
        """Return the canonical user-facing search URL for the given params.

        Useful for citation flows so a user can open the same search in the
        browser.
        """
        # Reuse the same param mapping as `search()` but return the URL.
        evs = kwargs.pop("events", None) or list(EVENT_TYPES)
        params = {
            "church-or-civil": kwargs.get("source", "all"),
            "lastname": kwargs.get("surname") or "",
            "firstname": kwargs.get("first_name") or "",
            "mothers-surname": kwargs.get("mothers_surname") or "",
            "location": kwargs.get("location") or "",
            "yearStart": kwargs.get("year_start") or "",
            "yearEnd": kwargs.get("year_end") or "",
        }
        for ev in evs:
            params[f"event-{ev}"] = 1
        return f"{BMD_BASE}/search/?" + urlencode(params)
