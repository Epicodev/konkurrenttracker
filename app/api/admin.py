"""Admin-endpoints til drift og debugging.

Bemaerk: ingen auth endnu - kommer i Sprint 04 (HTTP Basic Auth).
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends
from sqlmodel import Session, func, select

from app.analysis.classifier import classify_pending
from app.analysis.synthesizer import synthesize_week
from app.auth import require_basic_auth
from app.db import get_session
from app.models import CompanyEvent, Competitor, JobPosting, Signal
from app.scrapers.career_sites import CareerSiteScraper
from app.scrapers.cvr import CvrScraper
from app.scrapers.google_news import GoogleNewsScraper
from app.scrapers.jobindex import JobindexScraper
from app.scrapers.wayback import WaybackScraper

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_basic_auth)])


@router.get("/schedule")
def schedule_status() -> dict[str, Any]:
    """Lister scheduler-jobs og deres trigger-konfig."""
    from datetime import datetime

    from app.scheduler import SCHEDULE

    now = datetime.now()
    jobs = [
        {
            "id": f"scrape_{scraper.source}",
            "trigger": str(trigger),
            "next_run_local": str(trigger.get_next_fire_time(None, now)),
        }
        for scraper, trigger in SCHEDULE
    ]
    return {"jobs": jobs}


@router.get("/data-status")
def data_status(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Status pr. scraper-kilde: total antal, sidste-set timestamp, antal sidste 24t."""
    twenty_four_hours_ago = datetime.utcnow() - timedelta(hours=24)
    sources: dict[str, dict[str, Any]] = {}

    for source in session.exec(select(JobPosting.source).distinct()).all():
        total = session.exec(select(func.count()).select_from(JobPosting).where(JobPosting.source == source)).one()
        latest = session.exec(select(func.max(JobPosting.last_seen_at)).where(JobPosting.source == source)).one()
        last_24h = session.exec(
            select(func.count()).select_from(JobPosting).where(
                JobPosting.source == source,
                JobPosting.first_seen_at >= twenty_four_hours_ago,
            )
        ).one()
        sources[source] = {
            "kind": "job_postings",
            "total": total,
            "last_seen_at": latest.isoformat() if latest else None,
            "added_last_24h": last_24h,
        }

    for source in session.exec(select(CompanyEvent.source).distinct()).all():
        total = session.exec(select(func.count()).select_from(CompanyEvent).where(CompanyEvent.source == source)).one()
        latest = session.exec(select(func.max(CompanyEvent.detected_at)).where(CompanyEvent.source == source)).one()
        last_24h = session.exec(
            select(func.count()).select_from(CompanyEvent).where(
                CompanyEvent.source == source,
                CompanyEvent.detected_at >= twenty_four_hours_ago,
            )
        ).one()
        sources[source] = {
            "kind": "company_events",
            "total": total,
            "last_seen_at": latest.isoformat() if latest else None,
            "added_last_24h": last_24h,
        }

    competitors_total = session.exec(select(func.count()).select_from(Competitor)).one()
    competitors_active = session.exec(
        select(func.count()).select_from(Competitor).where(Competitor.active == True)  # noqa: E712
    ).one()
    competitors_with_cvr = session.exec(
        select(func.count()).select_from(Competitor).where(Competitor.cvr.is_not(None))  # type: ignore[union-attr]
    ).one()
    signals_total = session.exec(select(func.count()).select_from(Signal)).one()
    latest_signal_week = session.exec(select(func.max(Signal.week))).one()
    classified_jobs = session.exec(
        select(func.count()).select_from(JobPosting).where(JobPosting.category.is_not(None))  # type: ignore[union-attr]
    ).one()

    return {
        "competitors": {"total": competitors_total, "active": competitors_active, "with_cvr": competitors_with_cvr},
        "sources": sources,
        "analysis": {
            "signals_total": signals_total,
            "latest_signal_week": latest_signal_week,
            "classified_jobs": classified_jobs,
        },
    }


def _run_scraper(scraper: Any, source: str, session: Session) -> dict[str, Any]:
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
                "warnings": result.raw_warnings,
            }
        )
    return {
        "scraper": source,
        "competitors_processed": len(competitors),
        "total_added": sum(r["added"] for r in results),
        "results": results,
    }


@router.post("/scrape/jobindex")
def trigger_jobindex_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - koerer Jobindex-scraperen synkront for alle aktive konkurrenter."""
    return _run_scraper(JobindexScraper(), "jobindex", session)


@router.post("/scrape/cvr")
def trigger_cvr_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - koerer CVR-scraperen synkront for alle aktive konkurrenter med CVR-nummer."""
    return _run_scraper(CvrScraper(), "cvr", session)


@router.post("/scrape/google_news")
def trigger_google_news_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - koerer Google News-scraperen synkront for alle aktive konkurrenter."""
    return _run_scraper(GoogleNewsScraper(), "google_news", session)


@router.post("/scrape/career_sites")
def trigger_career_sites_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - koerer karriere-side scraperen for alle aktive konkurrenter med career_url."""
    return _run_scraper(CareerSiteScraper(), "career_page", session)


@router.post("/scrape/wayback")
def trigger_wayback_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - koerer wayback (web-snapshot) scraperen for alle aktive konkurrenter."""
    return _run_scraper(WaybackScraper(), "wayback", session)


@router.post("/scrape/all")
def trigger_all_scrapers(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - koerer alle 5 scrapere sekventielt. Bruges til ad-hoc rapport-trigger."""
    return {
        "jobindex": _run_scraper(JobindexScraper(), "jobindex", session),
        "cvr": _run_scraper(CvrScraper(), "cvr", session),
        "google_news": _run_scraper(GoogleNewsScraper(), "google_news", session),
        "career_sites": _run_scraper(CareerSiteScraper(), "career_page", session),
        "wayback": _run_scraper(WaybackScraper(), "wayback", session),
    }


@router.post("/analyze/classify")
def trigger_classify(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - klassificer pending jobopslag med Haiku."""
    return classify_pending(session)


@router.post("/analyze/synthesize")
def trigger_synthesize(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - generer ugentlig syntese med Sonnet."""
    classify_pending(session)
    return synthesize_week(session)


@router.get("/signals/latest")
def latest_signals(session: Session = Depends(get_session), limit: int = 20) -> list[dict[str, Any]]:
    """Seneste signaler i omvendt kronologisk orden."""
    rows = list(
        session.exec(
            select(Signal, Competitor)
            .join(Competitor, Signal.competitor_id == Competitor.id)
            .order_by(Signal.created_at.desc())  # type: ignore[union-attr]
            .limit(limit)
        ).all()
    )
    return [
        {
            "id": s.id,
            "week": s.week,
            "competitor": {"slug": c.slug, "name": c.name},
            "domain": s.domain,
            "severity": s.severity,
            "title": s.title,
            "summary": s.summary,
            "recommended_action": s.recommended_action,
            "recommended_owner": s.recommended_owner,
            "confidence": s.confidence,
            "source_refs": s.source_refs,
            "created_at": s.created_at.isoformat(),
        }
        for s, c in rows
    ]
