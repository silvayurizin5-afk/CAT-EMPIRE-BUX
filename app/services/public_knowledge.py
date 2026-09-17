from __future__ import annotations

import asyncio
from typing import Any

import httpx

_USER_AGENT = "NEXTBUY/1.0 (Discord assistant; public knowledge lookup)"


def _clean(value: object, limit: int = 1400) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


async def _wikipedia(client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
    response = await client.get(
        "https://pt.wikipedia.org/w/api.php",
        params={
            "action": "query",
            "generator": "search",
            "gsrsearch": query,
            "gsrlimit": 3,
            "prop": "extracts|info",
            "exintro": 1,
            "explaintext": 1,
            "inprop": "url",
            "format": "json",
            "formatversion": 2,
        },
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    data = response.json()
    pages = data.get("query", {}).get("pages", [])
    result: list[dict[str, str]] = []
    for page in pages[:3]:
        snippet = _clean(page.get("extract"))
        if not snippet:
            continue
        result.append(
            {
                "source": "Wikipedia",
                "title": _clean(page.get("title"), 180),
                "url": _clean(page.get("fullurl"), 500),
                "snippet": snippet,
            }
        )
    return result


async def _duckduckgo(client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
    response = await client.get(
        "https://api.duckduckgo.com/",
        params={
            "q": query,
            "format": "json",
            "no_html": 1,
            "skip_disambig": 1,
            "no_redirect": 1,
        },
        headers={"User-Agent": _USER_AGENT},
    )
    response.raise_for_status()
    data: dict[str, Any] = response.json()
    abstract = _clean(data.get("AbstractText"))
    if not abstract:
        return []
    return [
        {
            "source": "DuckDuckGo",
            "title": _clean(data.get("Heading") or query, 180),
            "url": _clean(data.get("AbstractURL"), 500),
            "snippet": abstract,
        }
    ]


async def fetch_public_knowledge(query: str) -> list[dict[str, str]]:
    """Busca contexto público gratuito. Falhas externas nunca impedem a resposta da IA."""
    cleaned = " ".join(query.split()).strip()
    if len(cleaned) < 3:
        return []

    timeout = httpx.Timeout(4.0, connect=2.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        results = await asyncio.gather(
            _wikipedia(client, cleaned),
            _duckduckgo(client, cleaned),
            return_exceptions=True,
        )

    merged: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group in results:
        if isinstance(group, BaseException):
            continue
        for item in group:
            key = (item.get("source", ""), item.get("title", ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
            if len(merged) >= 4:
                return merged
    return merged
