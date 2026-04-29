"""Google News RSS-scraper.

Henter https://news.google.com/rss/search?q=<query>&hl=da&gl=DK&ceid=DK:da pr. konkurrent.
Gemmer hver artikel som CompanyEvent (event_type='news', source='google_news').

Post-fetch filter: ligesom Jobindex returnerer Google News tangentielle resultater,
saa vi kraever at query-termen forekommer i title eller description.
"""

from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx
import structlog
from sqlmodel import Session, select

from app.models import CompanyEvent, Competitor
from app.scrapers.base import ScrapeResult, Scraper

logger = structlog.get_logger(__name__)

FEED_URL = "https://news.google.com/rss/search?q={query}&hl=da&gl=DK&ceid=DK:da"
HTTP_TIMEOUT = 20.0


def _query_for(competitor: Competitor) -> str | None:
    config: dict[str, Any] = competitor.scraper_config or {}
    explicit = config.get("google_news", {}).get("query")
    if explicit:
        return str(explicit)
    # Fald tilbage til jobindex-query hvis sat (typisk det rene firmanavn)
    return config.get("jobindex", {}).get("query")


def _parse_pub_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return parsedate_to_datetime(value).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


class GoogleNewsScraper(Scraper):
    source = "google_news"

    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        result = ScrapeResult(source=self.source, competitor_slug=competitor.slug)
        query = _query_for(competitor)
        if query is None:
            result.raw_warnings.append("ingen google_news-query konfigureret - sprunget over")
            return result

        url = FEED_URL.format(query=quote_plus(query))
        response = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        if feed.bozo and not feed.entries:
            result.error = f"Kunne ikke parse Google News RSS for {competitor.slug}: {feed.bozo_exception}"
            return result

        existing_ids = set(
            session.exec(
                select(CompanyEvent.external_id).where(
                    CompanyEvent.competitor_id == competitor.id,
                    CompanyEvent.source == self.source,
                )
            ).all()
        )

        now = datetime.utcnow()
        query_lower = query.lower()

        for entry in feed.entries:
            result.items_seen += 1
            entry_text = (str(entry.get("title", "")) + " " + str(entry.get("description", ""))).lower()
            if query_lower not in entry_text:
                continue

            external_id = entry.get("link") or entry.get("id")
            if not external_id or external_id in existing_ids:
                continue

            session.add(
                CompanyEvent(
                    competitor_id=competitor.id,  # type: ignore[arg-type]
                    event_type="news",
                    source=self.source,
                    external_id=external_id[:500],
                    title=str(entry.get("title", ""))[:500],
                    description=entry.get("description"),
                    url=external_id,
                    raw_data={
                        "title": entry.get("title"),
                        "link": entry.get("link"),
                        "published": entry.get("published"),
                        "source": entry.get("source", {}).get("title") if entry.get("source") else None,
                    },
                    occurred_at=_parse_pub_date(entry.get("published")),
                    detected_at=now,
                )
            )
            existing_ids.add(external_id)
            result.items_added += 1

        session.commit()
        logger.info(
            "scraper.run",
            source=self.source,
            slug=competitor.slug,
            query=query,
            seen=result.items_seen,
            added=result.items_added,
        )
        return result
