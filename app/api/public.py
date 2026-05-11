"""Read-endpoints til dashboard. HTTP Basic Auth-beskyttet.

Tilbyder filter-baseret list/detail af signals, jobs, events og stats.
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, func, select

from app.auth import require_basic_auth
from app.db import get_session
from app.models import (
    CompanyEvent,
    Competitor,
    FinancialReport,
    GeoMention,
    JobPosting,
    MarketJobPosting,
    MarketTrendSignal,
    Report,
    Signal,
)

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
    """Antal jobs pr. uge pr. kategori (på tværs af alle konkurrenter)."""
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


def _finance_row(report: FinancialReport, competitor: Competitor) -> dict[str, Any]:
    return {
        "competitor": {"slug": competitor.slug, "name": competitor.name},
        "fiscal_year_start": report.fiscal_year_start.isoformat() if report.fiscal_year_start else None,
        "fiscal_year_end": report.fiscal_year_end.isoformat(),
        "fiscal_year": report.fiscal_year_end.year,
        "revenue": report.revenue,
        "gross_profit": report.gross_profit,
        "profit_loss": report.profit_loss,
        "employee_expenses": report.employee_expenses,
        "equity": report.equity,
        "assets": report.assets,
        "average_employees": report.average_employees,
        "profit_margin": (
            report.profit_loss / report.revenue
            if report.profit_loss is not None and report.revenue and report.revenue > 0
            else None
        ),
        "equity_ratio": (
            report.equity / report.assets
            if report.equity is not None and report.assets and report.assets > 0
            else None
        ),
        "revenue_per_employee": (
            report.revenue / report.average_employees
            if report.revenue is not None and report.average_employees
            else None
        ),
        "pdf_url": report.pdf_url,
        "xbrl_url": report.xbrl_url,
        "published_at": report.published_at.isoformat() if report.published_at else None,
    }


@router.get("/finance/latest")
def finance_latest(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Seneste regnskab pr. konkurrent + YoY-vækst hvis året før er tilgængeligt."""
    rows = list(
        session.exec(
            select(FinancialReport, Competitor)
            .join(Competitor, FinancialReport.competitor_id == Competitor.id)
            .order_by(Competitor.name, FinancialReport.fiscal_year_end.desc())  # type: ignore[union-attr]
        ).all()
    )
    by_competitor: dict[int, list[tuple[FinancialReport, Competitor]]] = {}
    for r, c in rows:
        by_competitor.setdefault(c.id, []).append((r, c))

    out: list[dict[str, Any]] = []
    for _, reports in by_competitor.items():
        if not reports:
            continue
        latest, competitor = reports[0]
        prior = reports[1][0] if len(reports) > 1 else None
        data = _finance_row(latest, competitor)
        # YoY-vækst hvis både nuværende OG forrige har omsætning
        if prior and latest.revenue and prior.revenue:
            data["revenue_yoy"] = (latest.revenue - prior.revenue) / prior.revenue
        else:
            data["revenue_yoy"] = None
        if prior and latest.profit_loss is not None and prior.profit_loss is not None:
            data["profit_delta"] = latest.profit_loss - prior.profit_loss
        else:
            data["profit_delta"] = None
        data["report_count"] = len(reports)
        out.append(data)
    out.sort(key=lambda d: d["revenue"] or 0, reverse=True)
    return out


@router.get("/finance/history")
def finance_history(
    session: Session = Depends(get_session),
    competitor: str | None = None,
) -> dict[str, Any]:
    """Tidsserier af KPIs pr. konkurrent for graf-rendering."""
    query = (
        select(FinancialReport, Competitor)
        .join(Competitor, FinancialReport.competitor_id == Competitor.id)
        .order_by(FinancialReport.fiscal_year_end)  # type: ignore[union-attr]
    )
    if competitor:
        query = query.where(Competitor.slug == competitor)
    rows = list(session.exec(query).all())

    by_competitor: dict[str, dict[str, Any]] = {}
    for r, c in rows:
        entry = by_competitor.setdefault(
            c.name, {"competitor": {"slug": c.slug, "name": c.name}, "years": [], "revenue": [], "profit_loss": [], "equity": [], "employees": []}
        )
        entry["years"].append(r.fiscal_year_end.year)
        entry["revenue"].append(r.revenue)
        entry["profit_loss"].append(r.profit_loss)
        entry["equity"].append(r.equity)
        entry["employees"].append(r.average_employees)
    return {"series": list(by_competitor.values())}


@router.get("/market/overview")
def market_overview(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Overblik: totalt antal market-jobs, antal klassificerede, fordeling pr. kilde."""
    total = session.exec(select(func.count()).select_from(MarketJobPosting)).one()
    classified = session.exec(
        select(func.count()).select_from(MarketJobPosting).where(
            MarketJobPosting.classified_at.is_not(None)  # type: ignore[union-attr]
        )
    ).one()
    last_30d = datetime.utcnow() - timedelta(days=30)
    new_30d = session.exec(
        select(func.count()).select_from(MarketJobPosting).where(
            MarketJobPosting.first_seen_at >= last_30d
        )
    ).one()
    emerging = session.exec(
        select(func.count()).select_from(MarketJobPosting).where(
            MarketJobPosting.is_emerging.is_(True),  # type: ignore[union-attr]
            MarketJobPosting.first_seen_at >= last_30d,
        )
    ).one()
    return {
        "total_jobs": total,
        "classified": classified,
        "new_last_30d": new_30d,
        "emerging_last_30d": emerging,
    }


@router.get("/market/specializations")
def market_specializations(
    session: Session = Depends(get_session),
    weeks: int = Query(default=12, le=52),
) -> dict[str, Any]:
    """Time-series af specialiseringer pr. uge."""
    cutoff = datetime.utcnow() - timedelta(weeks=weeks)
    rows = list(
        session.exec(
            select(MarketJobPosting).where(
                MarketJobPosting.first_seen_at >= cutoff,
                MarketJobPosting.specialization.is_not(None),  # type: ignore[union-attr]
            )
        ).all()
    )
    series: dict[str, dict[str, int]] = {}
    weeks_set: set[str] = set()
    for j in rows:
        iso_year, iso_week, _ = j.first_seen_at.isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"
        weeks_set.add(week)
        spec = j.specialization or "other"
        series.setdefault(spec, {}).setdefault(week, 0)
        series[spec][week] += 1
    weeks_sorted = sorted(weeks_set)
    return {
        "weeks": weeks_sorted,
        "series": [
            {"name": name, "data": [series[name].get(w, 0) for w in weeks_sorted]}
            for name in sorted(series, key=lambda n: -sum(series[n].values()))
        ],
    }


@router.get("/market/top-skills")
def market_top_skills(
    session: Session = Depends(get_session),
    days: int = Query(default=30, le=180),
    limit: int = Query(default=20, le=100),
) -> dict[str, Any]:
    """Top teknologier i de seneste N dage + delta vs forrige N dage."""
    now = datetime.utcnow()
    current_start = now - timedelta(days=days)
    prior_start = current_start - timedelta(days=days)

    def _count_skills(start: datetime, end: datetime) -> dict[str, int]:
        rows = list(
            session.exec(
                select(MarketJobPosting).where(
                    MarketJobPosting.first_seen_at >= start,
                    MarketJobPosting.first_seen_at < end,
                    MarketJobPosting.classified_at.is_not(None),  # type: ignore[union-attr]
                )
            ).all()
        )
        counts: dict[str, int] = {}
        for r in rows:
            for tech in r.tech_stack or []:
                t = str(tech)
                counts[t] = counts.get(t, 0) + 1
        return counts

    current = _count_skills(current_start, now)
    prior = _count_skills(prior_start, current_start)

    skills = []
    for tech, count in current.items():
        prev = prior.get(tech, 0)
        delta_pct = ((count - prev) / prev) if prev > 0 else (1.0 if count > 0 else 0.0)
        skills.append({"tech": tech, "current": count, "prior": prev, "delta_pct": delta_pct})

    # Sortér efter current count desc
    skills.sort(key=lambda s: s["current"], reverse=True)
    return {
        "period_days": days,
        "current_start": current_start.isoformat(),
        "prior_start": prior_start.isoformat(),
        "skills": skills[:limit],
    }


@router.get("/market/emerging")
def market_emerging(
    session: Session = Depends(get_session),
    days: int = Query(default=60, le=180),
    limit: int = Query(default=30, le=100),
) -> list[dict[str, Any]]:
    """Nye/sjældne rolletyper (is_emerging=true) i de seneste N dage."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    rows = list(
        session.exec(
            select(MarketJobPosting)
            .where(
                MarketJobPosting.is_emerging.is_(True),  # type: ignore[union-attr]
                MarketJobPosting.first_seen_at >= cutoff,
            )
            .order_by(MarketJobPosting.first_seen_at.desc())  # type: ignore[union-attr]
            .limit(limit)
        ).all()
    )
    return [
        {
            "title": r.title,
            "company": r.company,
            "specialization": r.specialization,
            "tech_stack": r.tech_stack,
            "url": r.url,
            "first_seen_at": r.first_seen_at.isoformat(),
        }
        for r in rows
    ]


@router.get("/market/trend-signals")
def market_trend_signals(
    session: Session = Depends(get_session),
    week: str | None = None,
    limit: int = Query(default=20, le=50),
) -> list[dict[str, Any]]:
    """Sonnet-genererede markedstrend-signaler. Default: seneste uge med data."""
    target_week = week or session.exec(select(func.max(MarketTrendSignal.week))).one()
    if not target_week:
        return []
    rows = list(
        session.exec(
            select(MarketTrendSignal)
            .where(MarketTrendSignal.week == target_week)
            .order_by(MarketTrendSignal.severity, MarketTrendSignal.created_at.desc())  # type: ignore[union-attr]
            .limit(limit)
        ).all()
    )
    return [
        {
            "week": r.week,
            "signal_type": r.signal_type,
            "specialization": r.specialization,
            "tech": r.tech,
            "severity": r.severity,
            "title": r.title,
            "summary": r.summary,
            "delta_pct": r.delta_pct,
            "sample_size": r.sample_size,
            "recommended_action": r.recommended_action,
            "confidence": r.confidence,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


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
