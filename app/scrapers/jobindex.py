"""Jobindex RSS-scraper.

Henter feed pr. konkurrent fra https://www.jobindex.dk/jobsoegning.rss?q=<query>
og gemmer nye opslag som JobPosting-rows.
"""

from datetime import datetime
from urllib.parse import quote_plus

import feedparser
import httpx
import structlog
from sqlmodel import Session, select

from app.models import Competitor, JobPosting
from app.scrapers.base import ScrapeResult, Scraper, jobindex_query_for

logger = structlog.get_logger(__name__)

FEED_URL = "https://www.jobindex.dk/jobsoegning.rss?q={query}"
HTTP_TIMEOUT = 20.0


class JobindexScraper(Scraper):
    source = "jobindex"

    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        result = ScrapeResult(source=self.source, competitor_slug=competitor.slug)
        query = jobindex_query_for(competitor)
        if query is None:
            result.raw_warnings.append("ingen jobindex-query konfigureret - sprunget over")
            return result
        url = FEED_URL.format(query=quote_plus(query))

        response = httpx.get(url, timeout=HTTP_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        feed = feedparser.parse(response.content)

        if feed.bozo and not feed.entries:
            result.error = f"Kunne ikke parse RSS for {competitor.slug}: {feed.bozo_exception}"
            return result

        existing_external_ids = set(
            session.exec(
                select(JobPosting.external_id).where(
                    JobPosting.competitor_id == competitor.id,
                    JobPosting.source == self.source,
                )
            ).all()
        )

        now = datetime.utcnow()
        # Jobindex returnerer "lignende jobs" som fallback naar ingen entries matcher query'en
        # eksakt. Vi filtrerer post-fetch: query-termerne SKAL forekomme i title eller description.
        query_lower = query.lower()
        for entry in feed.entries:
            result.items_seen += 1
            entry_text = (str(entry.get("title", "")) + " " + str(entry.get("description", ""))).lower()
            if query_lower not in entry_text:
                continue

            external_id = entry.get("link") or entry.get("id")
            if not external_id:
                result.raw_warnings.append("entry uden link/id sprunget over")
                continue

            if external_id in existing_external_ids:
                # Opdater last_seen_at for eksisterende
                row = session.exec(
                    select(JobPosting).where(
                        JobPosting.competitor_id == competitor.id,
                        JobPosting.external_id == external_id,
                    )
                ).first()
                if row is not None:
                    row.last_seen_at = now
                    session.add(row)
                continue

            posting = JobPosting(
                competitor_id=competitor.id,  # type: ignore[arg-type]
                external_id=external_id,
                title=str(entry.get("title", ""))[:500],
                description=entry.get("description"),
                source=self.source,
                url=external_id,
                raw_data={
                    "title": entry.get("title"),
                    "link": entry.get("link"),
                    "categories": [t.term for t in entry.get("tags", [])] if entry.get("tags") else [],
                    "published": entry.get("published"),
                },
                first_seen_at=now,
                last_seen_at=now,
            )
            session.add(posting)
            result.items_added += 1
            existing_external_ids.add(external_id)

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
