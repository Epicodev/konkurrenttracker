"""Bygger ugens rapport som data-dictionary klar til template-rendering.

Sletter ikke noget - kombinerer signals + jobs + events + stats til en payload.
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, func, select

from app.models import CompanyEvent, Competitor, JobPosting, Signal


def _iso_week(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def build_payload(session: Session, week: str | None = None, days_back: int = 7) -> dict[str, Any]:
    week = week or _iso_week(datetime.utcnow())
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    competitors = {c.id: c for c in session.exec(select(Competitor)).all()}

    signal_rows = list(
        session.exec(
            select(Signal)
            .where(Signal.week == week)
            .order_by(Signal.severity, Signal.created_at.desc())  # type: ignore[union-attr]
        ).all()
    )
    signals = [
        {
            "competitor": {
                "slug": competitors[s.competitor_id].slug,
                "name": competitors[s.competitor_id].name,
            },
            "domain": s.domain,
            "severity": s.severity,
            "title": s.title,
            "summary": s.summary,
            "recommended_action": s.recommended_action,
            "recommended_owner": s.recommended_owner,
            "confidence": s.confidence,
        }
        for s in signal_rows
    ]

    # Jobs aggregeret pr. konkurrent
    job_rows = list(
        session.exec(select(JobPosting).where(JobPosting.first_seen_at >= cutoff)).all()
    )
    jobs_by_comp: dict[int, list[JobPosting]] = {}
    for j in job_rows:
        jobs_by_comp.setdefault(j.competitor_id, []).append(j)

    jobs_by_competitor = []
    for cid, jobs in sorted(jobs_by_comp.items(), key=lambda x: len(x[1]), reverse=True):
        if cid not in competitors:
            continue
        categories = Counter(j.category for j in jobs if j.category).most_common(3)
        jobs_by_competitor.append(
            {
                "name": competitors[cid].name,
                "count": len(jobs),
                "top_categories": [c for c, _ in categories],
                "senior_count": sum(1 for j in jobs if j.seniority == "senior"),
                "junior_count": sum(1 for j in jobs if j.seniority == "junior"),
            }
        )

    # Events i ugen
    event_rows = list(
        session.exec(
            select(CompanyEvent)
            .where(CompanyEvent.detected_at >= cutoff)
            .order_by(CompanyEvent.detected_at.desc())  # type: ignore[union-attr]
        ).all()
    )
    recent_events = [
        {
            "detected_date": e.detected_at.strftime("%d/%m"),
            "competitor_name": competitors.get(e.competitor_id, Competitor(slug="?", name="?")).name,
            "event_type": e.event_type,
            "title": e.title,
            "description": (e.description or "")[:300],
        }
        for e in event_rows
        if e.event_type != "web_change" and e.event_type != "web_baseline"
    ]
    web_changes = [
        {
            "competitor_name": competitors.get(e.competitor_id, Competitor(slug="?", name="?")).name,
            "title": e.title,
            "description": (e.description or "")[:400],
        }
        for e in event_rows
        if e.event_type in ("web_change", "web_baseline")
    ]

    stats = {
        "competitors": session.exec(
            select(func.count()).select_from(Competitor).where(Competitor.active == True)  # noqa: E712
        ).one(),
        "jobs_last_7d": len(job_rows),
        "events_last_7d": len(event_rows),
    }

    return {
        "week": week,
        "generated_date": datetime.utcnow().strftime("%d/%m/%Y"),
        "top_tagline": signals[0]["title"] if signals else None,
        "signals": signals,
        "jobs_by_competitor": jobs_by_competitor,
        "recent_events": recent_events,
        "web_changes": web_changes,
        "stats": stats,
    }
