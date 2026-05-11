"""Market-bred IT-job scraper.

Henter ALLE nye IT-jobopslag fra Jobindex (subid=2, IT-branche) og IT-Jobbank
(via flere keyword-queries) - uafhængigt af konkurrent-liste. Bruges til
marked-trends, ikke konkurrent-overvågning.

Idempotent via UniqueConstraint(source, external_id). Genkørsel opdaterer
last_seen_at på allerede sete opslag.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import feedparser
import httpx
import structlog
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from app.models import MarketJobPosting

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT = 20.0
USER_AGENT = "konkurrenttracker (epico.dk)"

# Jobindex IT-branche - flere sider for at få ~100+ jobs
JOBINDEX_BASE = "https://www.jobindex.dk/jobsoegning.rss?subid=2"
JOBINDEX_PAGES = 5  # 5 sider × 20 = ~100 unikke opslag

# IT-Jobbank har ikke en "alle IT"-feed - bredt sæt keywords for at få bred dækning
ITJOBBANK_BASE = "https://www.it-jobbank.dk/jobsoegning.rss?q={query}"
ITJOBBANK_QUERIES = [
    "udvikler", "konsulent", "ingeniør", "cloud", "DevOps", "data",
    "arkitekt", "security", "AI", "frontend", "backend", "fullstack",
    "Python", "Java", ".NET", "scrum", "produktejer", "tester", "support",
]


def _company_from_title(title: str) -> tuple[str, str]:
    """Jobindex-titler er typisk 'Jobtitel, Firmanavn'. Returner (jobtitel, firmanavn)."""
    parts = title.rsplit(",", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return title.strip(), ""


def _location_from_description(html: str | None) -> str | None:
    if not html:
        return None
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    # Søg efter " - <By>" mønster (jobindex har ofte by sidst)
    match = re.search(r"\b(København|Aarhus|Odense|Aalborg|Esbjerg|Randers|Vejle|Horsens|Roskilde|Herning|Hillerød|Helsingør|Lyngby|Glostrup|Holstebro|Silkeborg|Frederiksberg|Kolding|Fredericia|Næstved|Sønderborg|Viborg|Hjørring|Slagelse|Holbæk|Skive|Svendborg|Nyborg|Hvidovre|Ballerup|Greve|Albertslund)\b", text, re.IGNORECASE)
    return match.group(1) if match else None


def _ingest_feed_entries(
    source: str,
    feed_entries: list[Any],
    session: Session,
) -> tuple[int, int]:
    """Returner (seen, added)."""
    seen = 0
    added = 0
    now = datetime.utcnow()
    # Hent eksisterende eksterne id'er for at slippe for round-trips pr. entry
    existing_ids = set(
        session.exec(
            select(MarketJobPosting.external_id).where(MarketJobPosting.source == source)
        ).all()
    )

    for entry in feed_entries:
        seen += 1
        link = entry.get("link") or entry.get("id")
        if not link:
            continue
        external_id = link
        title_raw = str(entry.get("title", "")).strip()
        if not title_raw:
            continue
        job_title, company = _company_from_title(title_raw)
        description = entry.get("summary") or entry.get("description")
        location = _location_from_description(description)

        if external_id in existing_ids:
            # Opdater last_seen_at - billig "tjekket-recent"-markør
            row = session.exec(
                select(MarketJobPosting).where(
                    MarketJobPosting.source == source,
                    MarketJobPosting.external_id == external_id,
                )
            ).first()
            if row is not None:
                row.last_seen_at = now
                session.add(row)
            continue

        session.add(
            MarketJobPosting(
                source=source,
                external_id=external_id[:500],
                title=job_title[:500] or title_raw[:500],
                description=description,
                url=link,
                company=company[:200] if company else None,
                location=location,
                raw_data={
                    "title": title_raw,
                    "link": link,
                    "published": entry.get("published"),
                },
                first_seen_at=now,
                last_seen_at=now,
            )
        )
        added += 1
        existing_ids.add(external_id)

    session.commit()
    return seen, added


def scrape_jobindex_it(session: Session) -> dict[str, int]:
    """Hent IT-branche fra Jobindex over flere sider."""
    total_seen = 0
    total_added = 0
    for page in range(1, JOBINDEX_PAGES + 1):
        url = f"{JOBINDEX_BASE}&page={page}" if page > 1 else JOBINDEX_BASE
        try:
            response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("market.jobindex_page_failed", page=page, error=str(exc))
            continue
        feed = feedparser.parse(response.content)
        if not feed.entries:
            break  # ingen flere sider
        s, a = _ingest_feed_entries("jobindex_it", feed.entries, session)
        total_seen += s
        total_added += a
    logger.info("market.scraper.run", source="jobindex_it", seen=total_seen, added=total_added)
    return {"source": "jobindex_it", "seen": total_seen, "added": total_added}


def scrape_it_jobbank(session: Session) -> dict[str, int]:
    """Hent IT-Jobbank via flere keyword-queries for bred dækning."""
    total_seen = 0
    total_added = 0
    for query in ITJOBBANK_QUERIES:
        url = ITJOBBANK_BASE.format(query=quote_plus(query))
        try:
            response = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, follow_redirects=True)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.warning("market.it_jobbank_query_failed", query=query, error=str(exc))
            continue
        feed = feedparser.parse(response.content)
        if not feed.entries:
            continue
        s, a = _ingest_feed_entries("it_jobbank", feed.entries, session)
        total_seen += s
        total_added += a
    logger.info("market.scraper.run", source="it_jobbank", seen=total_seen, added=total_added)
    return {"source": "it_jobbank", "seen": total_seen, "added": total_added}


def scrape_market_jobs(session: Session) -> dict[str, Any]:
    """Kør begge market-scrapere sekventielt og returner samlet status."""
    a = scrape_jobindex_it(session)
    b = scrape_it_jobbank(session)
    return {
        "jobindex_it": a,
        "it_jobbank": b,
        "total_added": a["added"] + b["added"],
        "total_seen": a["seen"] + b["seen"],
    }
