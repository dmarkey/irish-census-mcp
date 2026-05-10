"""Thin async clients for the three Irish census APIs."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

# Base URLs
C26_BASE = "https://c26-api.nationalarchives.ie/api/census"
C19XX_BASE = "https://api-census.nationalarchives.ie/census"

USER_AGENT = "irish-census-mcp/0.1 (+https://github.com/dmarkey/irish-historical-census)"

# Hosts that get separate per-host concurrency caps.
C26_HOST = "c26-api.nationalarchives.ie"
C19XX_HOST = "api-census.nationalarchives.ie"


def _drop_none(params: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in params.items() if v is not None and v != ""}


class CensusHTTP:
    """Shared httpx.AsyncClient wrapper. One per process (or one per event loop).

    Per-host semaphores are created lazily on first use so they bind to the
    current event loop — important for test contexts that create a fresh loop
    per test function.
    """

    def __init__(self, timeout: float = 20.0, per_host_concurrency: int = 4) -> None:
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Origin": "https://nationalarchives.ie",
                "Referer": "https://nationalarchives.ie/",
            },
            follow_redirects=True,
        )
        self._per_host_concurrency = per_host_concurrency
        self._semaphores: dict[str, asyncio.Semaphore] = {}

    def _sem_for(self, host: str) -> asyncio.Semaphore:
        sem = self._semaphores.get(host)
        if sem is None:
            sem = asyncio.Semaphore(self._per_host_concurrency)
            self._semaphores[host] = sem
        return sem

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get(self, url: str, params: dict[str, Any], host: str) -> dict:
        async with self._sem_for(host):
            r = await self._client.get(url, params=_drop_none(params))
        if r.status_code >= 400:
            try:
                detail = r.json()
            except Exception:
                detail = r.text[:200]
            raise CensusAPIError(f"{r.status_code} from {url}: {detail}")
        return r.json()


class CensusAPIError(Exception):
    pass


# ---------------------------------------------------------------------------
# 1926 client
# ---------------------------------------------------------------------------


class Census1926:
    def __init__(self, http: CensusHTTP) -> None:
        self.http = http

    async def query(
        self,
        *,
        surname: str | None = None,
        first_name: str | None = None,
        county: str | None = None,
        townland: str | None = None,
        ded: str | None = None,
        sex: str | None = None,
        religion: str | None = None,
        age_min: int | None = None,
        age_max: int | None = None,
        image_group: int | None = None,
        a_id: int | None = None,
        fuzzy: bool = True,
        limit: int = 30,
        offset: int = 0,
    ) -> dict:
        surname_key = "surname__icontains" if fuzzy else "surname"
        first_name_key = "first_name__icontains" if fuzzy else "first_name"
        townland_key = "townland__icontains" if fuzzy else "townland"
        ded_key = "ded__icontains" if fuzzy else "ded"
        params = {
            surname_key: surname,
            first_name_key: first_name,
            "county": county,
            townland_key: townland,
            ded_key: ded,
            "updated_sex": sex,
            "updated_religion": religion,
            "updated_age__gte": age_min,
            "updated_age__lte": age_max,
            "image_group": image_group,
            "a_id": a_id,
            "limit": limit,
            "offset": offset,
        }
        return await self.http.get(f"{C26_BASE}/query_c26a", params, C26_HOST)

    async def related_images(self, image_group: int) -> dict:
        return await self.http.get(
            f"{C26_BASE}/related_images", {"image_group": image_group}, C26_HOST
        )

    def image_url(self, aform_name: str) -> str:
        return f"{C26_BASE}/image_c26/{aform_name}"


# ---------------------------------------------------------------------------
# 1901 / 1911 client
# ---------------------------------------------------------------------------


class Census19011911:
    def __init__(self, http: CensusHTTP) -> None:
        self.http = http

    async def query(
        self,
        *,
        surname: str | None = None,
        firstname: str | None = None,
        census_year: int | None = None,
        county: str | None = None,
        ded: str | None = None,
        townland: str | None = None,
        sex: str | None = None,
        religion_updated: str | None = None,
        age_min: int | None = None,
        age_max: int | None = None,
        image_group: str | int | None = None,
        id_: int | None = None,
        fuzzy: bool = True,
        limit: int = 30,
        offset: int = 0,
    ) -> dict:
        surname_key = "surname__icontains" if fuzzy else "surname"
        firstname_key = "firstname__icontains" if fuzzy else "firstname"
        ded_key = "ded__icontains" if fuzzy else "ded"
        townland_key = "townland__icontains" if fuzzy else "townland"
        params = {
            surname_key: surname,
            firstname_key: firstname,
            "census_year": census_year,
            "county": county,
            ded_key: ded,
            townland_key: townland,
            "sex": sex,
            "religion_updated": religion_updated,
            "age__gte": age_min,
            "age__lte": age_max,
            "image_group": str(image_group) if image_group is not None else None,
            "id": id_,
            "limit": limit,
            "offset": offset,
        }
        return await self.http.get(f"{C19XX_BASE}/query", params, C19XX_HOST)

    def image_url(self, nai_id: str) -> str:
        nai_id = nai_id.removesuffix(".pdf")
        return f"{C19XX_BASE}/image/{nai_id}.pdf"


# ---------------------------------------------------------------------------
# Pre-Famine (1821-1851) client
# ---------------------------------------------------------------------------


class Census19th:
    def __init__(self, http: CensusHTTP) -> None:
        self.http = http

    async def query(
        self,
        *,
        surname: str | None = None,
        firstname: str | None = None,
        census_year: int | None = None,
        county: str | None = None,
        barony: str | None = None,
        parish: str | None = None,
        townland: str | None = None,
        hoh_flag: bool | None = None,
        id_: int | None = None,
        fuzzy: bool = True,
        limit: int = 30,
        offset: int = 0,
    ) -> dict:
        surname_key = "surname__icontains" if fuzzy else "surname"
        firstname_key = "firstname__icontains" if fuzzy else "firstname"
        parish_key = "parish__icontains" if fuzzy else "parish"
        townland_key = "townland__icontains" if fuzzy else "townland"
        params = {
            surname_key: surname,
            firstname_key: firstname,
            "census_year": census_year,
            "county": county,
            "barony": barony,
            parish_key: parish,
            townland_key: townland,
            "hoh_flag": str(hoh_flag).lower() if hoh_flag is not None else None,
            "id": id_,
            "limit": limit,
            "offset": offset,
        }
        return await self.http.get(f"{C19XX_BASE}/query_c19", params, C19XX_HOST)

    def image_url(self, image_id: str) -> str:
        image_id = image_id.removesuffix(".pdf")
        return f"{C19XX_BASE}/image/{image_id}.pdf"
