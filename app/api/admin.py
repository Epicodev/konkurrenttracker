"""Admin-endpoints til drift og debugging.

Bemaerk: ingen auth endnu - kommer i Sprint 04 (HTTP Basic Auth).
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.db import get_session
from app.models import Competitor, JobPosting
from app.scrapers.jobindex import JobindexScraper

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/data-status")
def data_status(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Status pr. scraper-kilde: total antal, sidste-set timestamp, antal sidste 24t."""
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    sources_in_db = session.exec(select(JobPosting.source).distinct()).all()
    sources: dict[str, dict[str, Any]] = {}

    for source in sources_in_db:
        total = session.exec(select(func.count()).select_from(JobPosting).where(JobPosting.source == source)).one()
        latest = session.exec(
            select(func.max(JobPosting.last_seen_at)).where(JobPosting.source == source)
        ).one()
        last_24h = session.exec(
            select(func.count()).select_from(JobPosting).where(
                JobPosting.source == source,
                JobPosting.first_seen_at >= twenty_four_hours_ago,
            )
        ).one()
        sources[source] = {
            "total": total,
            "last_seen_at": latest.isoformat() if latest else None,
            "added_last_24h": last_24h,
        }

    competitors_total = session.exec(select(func.count()).select_from(Competitor)).one()
    competitors_active = session.exec(
        select(func.count()).select_from(Competitor).where(Competitor.active == True)  # noqa: E712
    ).one()

    return {
        "competitors": {"total": competitors_total, "active": competitors_active},
        "sources": sources,
    }


@router.post("/scrape/jobindex")
def trigger_jobindex_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - koerer Jobindex-scraperen synkront for alle aktive konkurrenter."""
    scraper = JobindexScraper()
    competitors = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712
    results = []
    for competitor in competitors:
        result = scraper.safe_scrape(competitor, session)
        results.append(
            {
                "slug": result.competitor_slug,
                "seen": result.items_seen,
                "added": result.items_added,
                "error": result.error,
            }
        )
    return {
        "scraper": "jobindex",
        "competitors_processed": len(competitors),
        "total_added": sum(r["added"] for r in results),
        "results": results,
    }
