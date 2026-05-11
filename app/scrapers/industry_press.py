"""Industri-presse scraper.

Henter RSS-feeds fra danske + internationale IT-medier. Adskilt fra konkurrent-
specifik Google News - dette er HELE feedet uden firma-filter, så vi kan
identificere brede markedstemaer.

Computerworld.dk og ITwatch.dk har ikke offentlige RSS i 2026 (paywall) -
de er udeladt. Berlingske bruger Google News som proxy.
"""

from __future__ import annotations

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

import feedparser
import httpx
import structlog
from sqlmodel import Session, select

from app.models import IndustryArticle

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT = 20.0
USER_AGENT = "Mozilla/5.0 (compatible; konkurrenttracker/1.0; +https://epico.dk)"

# (source-slug, RSS-URL, geo-default).
# geo-default bruges som hint til Haiku - kan overskrives pr. artikel ved klassificering.
FEEDS: list[tuple[str, str, str]] = [
    ("version2", "https://www.version2.dk/rss", "dk"),
    ("borsen", "https://borsen.dk/rss/", "dk"),
    ("it_branchen", "https://itb.dk/feed/", "dk"),
    ("berlingske", "https://news.google.com/rss/search?q=site:berlingske.dk+business&hl=da&gl=DK&ceid=DK:da", "dk"),
    ("tech_eu", "https://tech.eu/feed/", "eu"),
    ("the_register", "https://www.theregister.com/headlines.atom", "global"),
    ("techcrunch", "https://techcrunch.com/feed/", "global"),
    ("siliconangle", "https://siliconangle.com/feed/", "global"),
]


def _parse_pub_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _ingest_feed(source: str, feed_url: str, geo_default: str, session: Session) -> dict[str, Any]:
    result = {"source": source, "seen": 0, "added": 0, "error": None}
    try:
        response = httpx.get(
            feed_url,
            headers={"User-Agent": USER_AGENT},
            timeout=HTTP_TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        result["error"] = f"http_error: {exc}"
        logger.warning("industry_press.fetch_failed", source=source, error=str(exc))
        return result

    feed = feedparser.parse(response.content)
    if feed.bozo and not feed.entries:
        result["error"] = f"parse_failed: {feed.bozo_exception}"
        return result

    existing_ids = set(
        session.exec(
            select(IndustryArticle.external_id).where(IndustryArticle.source == source)
        ).all()
    )

    now = datetime.utcnow()
    for entry in feed.entries:
        result["seen"] += 1
        external_id = entry.get("link") or entry.get("id")
        if not external_id or external_id in existing_ids:
            continue

        title = str(entry.get("title", "")).strip()
        if not title:
            continue

        description = entry.get("summary") or entry.get("description")
        # Strip HTML hvis det er i description
        if description and "<" in description:
            from bs4 import BeautifulSoup
            description = BeautifulSoup(description, "lxml").get_text(" ", strip=True)
        if description:
            description = description[:2000]

        session.add(
            IndustryArticle(
                source=source,
                external_id=external_id[:500],
                title=title[:500],
                description=description,
                url=external_id,
                raw_data={
                    "link": external_id,
                    "published": entry.get("published"),
                    "geo_default": geo_default,
                    "feed_source": entry.get("source", {}).get("title") if entry.get("source") else None,
                },
                published_at=_parse_pub_date(entry.get("published")),
                first_seen_at=now,
            )
        )
        existing_ids.add(external_id)
        result["added"] += 1

    session.commit()
    logger.info("industry_press.fetch", **{k: v for k, v in result.items() if v is not None})
    return result


def scrape_industry_press(session: Session) -> dict[str, Any]:
    """Kør alle industri-feed scrapere sekventielt. Returner samlet status."""
    per_source: list[dict[str, Any]] = []
    total_added = 0
    total_seen = 0
    failed = 0
    for source, url, geo in FEEDS:
        r = _ingest_feed(source, url, geo, session)
        per_source.append(r)
        total_added += r["added"]
        total_seen += r["seen"]
        if r["error"]:
            failed += 1
    return {
        "sources": per_source,
        "total_added": total_added,
        "total_seen": total_seen,
        "failed_sources": failed,
    }
