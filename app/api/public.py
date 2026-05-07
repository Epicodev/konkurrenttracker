"""Read-endpoints til dashboard. HTTP Basic Auth-beskyttet.

Tilbyder filter-baseret list/detail af signals, jobs, events og stats.
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.auth import require_basic_auth
from app.db import get_session
from app.models import CompanyEvent, Competitor, GeoMention, JobPosting, Report, Signal

router = APIRouter(prefix="/api", tags=["public"], dependencies=[Depends(require_basic_auth)])


@router.get("/competitors")
def list_competitors(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712
    return [
        {"id": c.id, "slug": c.slug, "name": c.name, "cvr": c.cvr, "domain": c.domain} for c in rows
    ]


@router.get("/signals")
def list_signals(
    session: Session = Depends(get_session),
    week: str | None = None,
    competitor: str | None = None,
    severity: str | None = None,
    limit: int = Query(default=50, le=200),
) -> list[dict[str, Any]]:
    query = (
        select(Signal, Competitor)
        .join(Competitor, Signal.competitor_id == Competitor.id)
        .order_by(Signal.created_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    )
    if week:
        query = query.where(Signal.week == week)
    if competitor:
        query = query.where(Competitor.slug == competitor)
    if severity:
        query = query.where(Signal.severity == severity)

    rows = list(session.exec(query).all())
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


@router.get("/jobs")
def list_jobs(
    session: Session = Depends(get_session),
    competitor: str | None = None,
    source: str | None = None,
    days: int = Query(default=30, le=365),
    limit: int = Query(default=100, le=500),
) -> list[dict[str, Any]]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        select(JobPosting, Competitor)
        .join(Competitor, JobPosting.competitor_id == Competitor.id)
        .where(JobPosting.first_seen_at >= cutoff)
        .order_by(JobPosting.first_seen_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    )
    if competitor:
        query = query.where(Competitor.slug == competitor)
    if source:
        query = query.where(JobPosting.source == source)

    rows = list(session.exec(query).all())
    return [
        {
            "id": j.id,
            "competitor": {"slug": c.slug, "name": c.name},
            "title": j.title,
            "category": j.category,
            "seniority": j.seniority,
            "is_freelance": j.is_freelance,
            "location": j.location,
            "source": j.source,
            "url": j.url,
            "first_seen_at": j.first_seen_at.isoformat(),
        }
        for j, c in rows
    ]


@router.get("/events")
def list_events(
    session: Session = Depends(get_session),
    competitor: str | None = None,
    source: str | None = None,
    days: int = Query(default=30, le=365),
    limit: int = Query(default=100, le=500),
) -> list[dict[str, Any]]:
    cutoff = datetime.utcnow() - timedelta(days=days)
    query = (
        select(CompanyEvent, Competitor)
        .join(Competitor, CompanyEvent.competitor_id == Competitor.id)
        .where(CompanyEvent.detected_at >= cutoff)
        .order_by(CompanyEvent.detected_at.desc())  # type: ignore[union-attr]
        .limit(limit)
    )
    if competitor:
        query = query.where(Competitor.slug == competitor)
    if source:
        query = query.where(CompanyEvent.source == source)

    rows = list(session.exec(query).all())
    return [
        {
            "id": e.id,
            "competitor": {"slug": c.slug, "name": c.name},
            "event_type": e.event_type,
            "source": e.source,
            "title": e.title,
            "description": e.description,
            "url": e.url,
            "detected_at": e.detected_at.isoformat(),
        }
        for e, c in rows
    ]


@router.get("/reports")
def list_reports(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    rows = list(session.exec(select(Report).order_by(Report.generated_at.desc())).all())  # type: ignore[union-attr]
    return [
        {
            "week": r.week,
            "status": r.status,
            "signal_count": r.signal_count,
            "data_points": r.data_points,
            "exec_summary": r.exec_summary,
            "generated_at": r.generated_at.isoformat(),
            "sent_at": r.sent_at.isoformat() if r.sent_at else None,
        }
        for r in rows
    ]


@router.get("/trends/jobs")
def trend_jobs(
    session: Session = Depends(get_session),
    days: int = Query(default=90, le=365),
) -> dict[str, Any]:
    """Antal nye jobopslag pr. uge (ISO) pr. konkurrent. Bruges til linje-graf."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = list(
        session.exec(
            select(JobPosting, Competitor)
            .join(Competitor, JobPosting.competitor_id == Competitor.id)
            .where(JobPosting.first_seen_at >= cutoff)
        ).all()
    )
    series: dict[str, dict[str, int]] = {}
    weeks: set[str] = set()
    for j, c in rows:
        iso_year, iso_week, _ = j.first_seen_at.isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"
        weeks.add(week)
        series.setdefault(c.name, {}).setdefault(week, 0)
        series[c.name][week] += 1
    weeks_sorted = sorted(weeks)
    return {
        "weeks": weeks_sorted,
        "series": [
            {"name": name, "data": [series[name].get(w, 0) for w in weeks_sorted]}
            for name in sorted(series)
        ],
    }


@router.get("/trends/jobs-by-category")
def trend_jobs_by_category(
    session: Session = Depends(get_session),
    days: int = Query(default=90, le=365),
) -> dict[str, Any]:
    """Antal jobs pr. uge pr. kategori (paa tvaers af alle konkurrenter)."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = list(session.exec(select(JobPosting).where(JobPosting.first_seen_at >= cutoff)).all())
    series: dict[str, dict[str, int]] = {}
    weeks: set[str] = set()
    for j in rows:
        iso_year, iso_week, _ = j.first_seen_at.isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"
        weeks.add(week)
        category = j.category or "Ukategoriseret"
        series.setdefault(category, {}).setdefault(week, 0)
        series[category][week] += 1
    weeks_sorted = sorted(weeks)
    return {
        "weeks": weeks_sorted,
        "series": [
            {"name": name, "data": [series[name].get(w, 0) for w in weeks_sorted]}
            for name in sorted(series)
        ],
    }


@router.get("/geo/latest")
def geo_latest(
    session: Session = Depends(get_session),
    week: str | None = None,
) -> dict[str, Any]:
    """GEO share-of-voice - default: seneste uge med data."""
    target_week = week or session.exec(select(func.max(GeoMention.week))).one()
    if not target_week:
        return {"week": None, "rows": []}
    rows = list(
        session.exec(
            select(GeoMention, Competitor)
            .join(Competitor, GeoMention.competitor_id == Competitor.id)
            .where(GeoMention.week == target_week)
            .order_by(GeoMention.share_of_voice.desc())  # type: ignore[union-attr]
        ).all()
    )
    return {
        "week": target_week,
        "rows": [
            {
                "competitor": {"slug": c.slug, "name": c.name},
                "ai_engine": g.ai_engine,
                "mentions": g.mentions,
                "total_queries": g.total_queries,
                "share_of_voice": g.share_of_voice,
                "sentiment": g.sentiment,
                "sample_quotes": g.sample_quotes,
                "created_at": g.created_at.isoformat(),
            }
            for g, c in rows
        ],
    }


@router.get("/geo/trends")
def geo_trends(
    session: Session = Depends(get_session),
    weeks: int = Query(default=12, le=52),
) -> dict[str, Any]:
    """Share-of-voice over tid pr. konkurrent."""
    rows = list(
        session.exec(
            select(GeoMention, Competitor)
            .join(Competitor, GeoMention.competitor_id == Competitor.id)
        ).all()
    )
    by_week: dict[str, dict[str, float]] = {}
    weeks_set: set[str] = set()
    for g, c in rows:
        weeks_set.add(g.week)
        by_week.setdefault(c.name, {})[g.week] = g.share_of_voice
    sorted_weeks = sorted(weeks_set)[-weeks:]
    return {
        "weeks": sorted_weeks,
        "series": [
            {"name": name, "data": [by_week[name].get(w, 0.0) for w in sorted_weeks]}
            for name in sorted(by_week)
        ],
    }


@router.get("/stats")
def stats(session: Session = Depends(get_session)) -> dict[str, Any]:
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    return {
        "competitors": session.exec(
            select(func.count()).select_from(Competitor).where(Competitor.active == True)  # noqa: E712
        ).one(),
        "jobs_total": session.exec(select(func.count()).select_from(JobPosting)).one(),
        "jobs_last_7d": session.exec(
            select(func.count()).select_from(JobPosting).where(JobPosting.first_seen_at >= seven_days_ago)
        ).one(),
        "events_total": session.exec(select(func.count()).select_from(CompanyEvent)).one(),
        "events_last_7d": session.exec(
            select(func.count()).select_from(CompanyEvent).where(CompanyEvent.detected_at >= seven_days_ago)
        ).one(),
        "signals_total": session.exec(select(func.count()).select_from(Signal)).one(),
        "latest_signal_week": session.exec(select(func.max(Signal.week))).one(),
    }
