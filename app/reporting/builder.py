"""Bygger ugens rapport som data-dictionary klar til template-rendering.

Kombinerer ALLE datatyper til én CEO-læsbar payload:
- Auto-genereret TL;DR (3-5 vigtigste must-knows)
- Signaler (sorteret efter severity)
- Markedstrends (Sonnet)
- Industri-puls (Sonnet)
- Finansielt overblik (XBRL-noegletal pr. konkurrent)
- GEO share-of-voice
- Bilag: jobs, events, web-aendringer
"""

from collections import Counter
from datetime import datetime, timedelta
from typing import Any

from sqlmodel import Session, func, select

from app.models import (
    CompanyEvent,
    Competitor,
    FinancialReport,
    GeoMention,
    JobPosting,
    MarketJobPosting,
    MarketTrendSignal,
    Signal,
)


def _iso_week(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


SEVERITY_ORDER = {"urgent": 0, "signal": 1, "opportunity": 2}


def _fmt_dkk(value: float | None) -> str:
    if value is None:
        return "—"
    abs_v = abs(value)
    if abs_v >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f} mia"
    if abs_v >= 1_000_000:
        return f"{value / 1_000_000:.1f} mio"
    if abs_v >= 1_000:
        return f"{value / 1_000:.0f} tkr"
    return f"{value:.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "—"
    arrow = "▲" if value > 0.005 else "▼" if value < -0.005 else "—"
    return f"{arrow} {value * 100:.1f}%"


def _gather_signals(session: Session, week: str, competitors: dict[int, Competitor]) -> list[dict[str, Any]]:
    signal_rows = list(
        session.exec(
            select(Signal)
            .where(Signal.week == week)
        ).all()
    )
    # Sort: urgent first, så signal, så opportunity. Inden for samme severity: nyeste først.
    signal_rows.sort(key=lambda s: (SEVERITY_ORDER.get(s.severity, 99), -(s.created_at.timestamp() if s.created_at else 0)))
    return [
        {
            "competitor": {
                "slug": competitors[s.competitor_id].slug if s.competitor_id in competitors else "?",
                "name": competitors[s.competitor_id].name if s.competitor_id in competitors else "?",
            },
            "domain": s.domain,
            "severity": s.severity,
            "title": s.title,
            "summary": s.summary,
            "recommended_action": s.recommended_action,
            "recommended_owner": s.recommended_owner,
            "confidence": s.confidence,
        }
        for s in signal_rows[:12]  # cap ved 12 - over det bliver det datadump
    ]


def _gather_market_trends(session: Session, week: str) -> list[dict[str, Any]]:
    rows = list(
        session.exec(
            select(MarketTrendSignal)
            .where(
                MarketTrendSignal.week == week,
                MarketTrendSignal.signal_type != "industry_pulse",
            )
        ).all()
    )
    rows.sort(key=lambda r: (SEVERITY_ORDER.get(r.severity, 99), -(r.delta_pct or 0)))
    return [
        {
            "signal_type": r.signal_type,
            "specialization": r.specialization,
            "tech": r.tech,
            "severity": r.severity,
            "title": r.title,
            "summary": r.summary,
            "delta_pct": r.delta_pct,
            "delta_pct_str": _fmt_pct(r.delta_pct) if r.delta_pct is not None else None,
            "sample_size": r.sample_size,
            "recommended_action": r.recommended_action,
            "confidence": r.confidence,
        }
        for r in rows[:6]
    ]


def _gather_industry_pulse(session: Session, week: str) -> list[dict[str, Any]]:
    rows = list(
        session.exec(
            select(MarketTrendSignal)
            .where(
                MarketTrendSignal.week == week,
                MarketTrendSignal.signal_type == "industry_pulse",
            )
        ).all()
    )
    rows.sort(key=lambda r: SEVERITY_ORDER.get(r.severity, 99))
    return [
        {
            "topic": r.specialization,
            "title": r.title,
            "summary": r.summary,
            "severity": r.severity,
            "sample_size": r.sample_size,
            "recommended_action": r.recommended_action,
            "confidence": r.confidence,
            "geo_scope": (r.source_refs or {}).get("geo_scope"),
            "recommended_owner": (r.source_refs or {}).get("recommended_owner"),
            "mentioned_competitors": (r.source_refs or {}).get("mentioned_competitors", []),
        }
        for r in rows[:5]
    ]


def _gather_finance(session: Session, competitors: dict[int, Competitor]) -> dict[str, Any]:
    """Pr. konkurrent: seneste regnskab + YoY hvis muligt."""
    all_reports = list(session.exec(select(FinancialReport).order_by(FinancialReport.fiscal_year_end.desc())).all())  # type: ignore[union-attr]
    by_competitor: dict[int, list[FinancialReport]] = {}
    for r in all_reports:
        by_competitor.setdefault(r.competitor_id, []).append(r)

    rows: list[dict[str, Any]] = []
    for cid, reports in by_competitor.items():
        if cid not in competitors:
            continue
        latest = reports[0]
        prior = reports[1] if len(reports) > 1 else None
        yoy = None
        if prior and latest.revenue and prior.revenue:
            yoy = (latest.revenue - prior.revenue) / prior.revenue
        margin = None
        if latest.profit_loss is not None and latest.revenue and latest.revenue > 0:
            margin = latest.profit_loss / latest.revenue
        equity_ratio = None
        if latest.equity is not None and latest.assets and latest.assets > 0:
            equity_ratio = latest.equity / latest.assets
        rev_per_fte = None
        if latest.revenue is not None and latest.average_employees:
            rev_per_fte = latest.revenue / latest.average_employees
        rows.append(
            {
                "competitor_name": competitors[cid].name,
                "fiscal_year": latest.fiscal_year_end.year,
                "revenue": latest.revenue,
                "revenue_str": _fmt_dkk(latest.revenue),
                "yoy": yoy,
                "yoy_str": _fmt_pct(yoy) if yoy is not None else "—",
                "profit_loss": latest.profit_loss,
                "profit_loss_str": _fmt_dkk(latest.profit_loss),
                "margin": margin,
                "margin_str": _fmt_pct(margin) if margin is not None else "—",
                "equity": latest.equity,
                "equity_str": _fmt_dkk(latest.equity),
                "equity_ratio": equity_ratio,
                "equity_ratio_str": _fmt_pct(equity_ratio) if equity_ratio is not None else "—",
                "employees": latest.average_employees,
                "rev_per_fte_str": _fmt_dkk(rev_per_fte),
            }
        )
    # Top 10 efter omsaetning (None til sidst)
    top_revenue = sorted(
        [r for r in rows if r["revenue"] is not None],
        key=lambda r: r["revenue"] or 0,
        reverse=True,
    )[:10]
    # Top vaekst (kun hvor YoY findes)
    top_growth = sorted(
        [r for r in rows if r["yoy"] is not None],
        key=lambda r: r["yoy"] or 0,
        reverse=True,
    )[:5]
    # Bottom growth (varseler)
    bottom_growth = sorted(
        [r for r in rows if r["yoy"] is not None],
        key=lambda r: r["yoy"] or 0,
    )[:3]
    return {
        "all_rows": sorted(rows, key=lambda r: r["revenue"] or 0, reverse=True),
        "top_revenue": top_revenue,
        "top_growth": top_growth,
        "bottom_growth": bottom_growth,
    }


def _gather_geo(session: Session, week: str, competitors: dict[int, Competitor]) -> list[dict[str, Any]]:
    rows = list(
        session.exec(
            select(GeoMention).where(GeoMention.week == week)
        ).all()
    )
    rows.sort(key=lambda r: r.share_of_voice, reverse=True)
    return [
        {
            "competitor_name": competitors[r.competitor_id].name if r.competitor_id in competitors else "?",
            "mentions": r.mentions,
            "total_queries": r.total_queries,
            "share_of_voice": r.share_of_voice,
            "share_of_voice_str": f"{r.share_of_voice * 100:.0f}%",
            "sentiment": r.sentiment,
        }
        for r in rows
        if r.share_of_voice > 0  # skip 0-mentions for renlighed
    ][:10]


def _gather_top_skills(session: Session, days: int = 30) -> list[dict[str, Any]]:
    """Top 10 voksende tech-skills i markedet baseret paa MarketJobPosting."""
    cutoff = datetime.utcnow() - timedelta(days=days)
    jobs = list(
        session.exec(
            select(MarketJobPosting).where(
                MarketJobPosting.first_seen_at >= cutoff,
                MarketJobPosting.classified_at.is_not(None),  # type: ignore[union-attr]
            )
        ).all()
    )
    skill_counts: Counter = Counter()
    for j in jobs:
        for tech in j.tech_stack or []:
            skill_counts[str(tech)] += 1
    return [
        {"skill": skill, "count": count}
        for skill, count in skill_counts.most_common(10)
    ]


def _build_tldr(
    signals: list[dict[str, Any]],
    market_trends: list[dict[str, Any]],
    industry_pulse: list[dict[str, Any]],
    finance: dict[str, Any],
) -> list[str]:
    """Auto-generer 3-5 bullets med ugens vigtigste fra de hoejest-prioriterede items."""
    bullets: list[str] = []

    # 1-3 urgent signaler foerst
    urgent_signals = [s for s in signals if s["severity"] == "urgent"]
    for s in urgent_signals[:3]:
        bullets.append(f"🔴 {s['competitor']['name']}: {s['title']}")

    # 1-2 markedstrend hvis vi har plads
    if len(bullets) < 5:
        for t in market_trends[:2]:
            if len(bullets) >= 5:
                break
            arrow = "📈" if (t.get("delta_pct") or 0) > 0 else "📉"
            bullets.append(f"{arrow} Marked: {t['title']}")

    # 1 industri-puls hvis plads
    if len(bullets) < 5 and industry_pulse:
        p = industry_pulse[0]
        bullets.append(f"📰 Industri: {p['title']}")

    # Finansielt highlight: storste falde/stigning
    if len(bullets) < 5 and finance.get("bottom_growth"):
        worst = finance["bottom_growth"][0]
        if worst["yoy"] is not None and worst["yoy"] < -0.05:
            bullets.append(f"📊 {worst['competitor_name']}: omsaetning {worst['yoy_str']} - foelg op")

    # Fallback: hvis ingen urgent, brug top signal
    if not bullets and signals:
        top = signals[0]
        bullets.append(f"⚠️ {top['competitor']['name']}: {top['title']}")

    return bullets[:5]


def build_payload(session: Session, week: str | None = None, days_back: int = 7) -> dict[str, Any]:
    week = week or _iso_week(datetime.utcnow())
    cutoff = datetime.utcnow() - timedelta(days=days_back)

    competitors = {c.id: c for c in session.exec(select(Competitor)).all()}

    signals = _gather_signals(session, week, competitors)
    market_trends = _gather_market_trends(session, week)
    industry_pulse = _gather_industry_pulse(session, week)
    finance = _gather_finance(session, competitors)
    geo = _gather_geo(session, week, competitors)
    top_skills = _gather_top_skills(session, days=30)

    # Jobs pr. konkurrent (bilag)
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
                "top_categories": [c for c, _ in categories if c],
                "senior_count": sum(1 for j in jobs if j.seniority == "senior"),
                "junior_count": sum(1 for j in jobs if j.seniority == "junior"),
            }
        )

    # Events i ugen (bilag) - excl. web-snapshots (de er noisy)
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
        if e.event_type not in ("web_change", "web_baseline")
    ][:30]
    web_changes = [
        {
            "competitor_name": competitors.get(e.competitor_id, Competitor(slug="?", name="?")).name,
            "title": e.title,
            "description": (e.description or "")[:400],
        }
        for e in event_rows
        if e.event_type in ("web_change", "web_baseline")
    ][:10]

    # Tæl signaler pr. severity til hero-stats
    severity_counts = Counter(s["severity"] for s in signals)

    stats = {
        "competitors": session.exec(
            select(func.count()).select_from(Competitor).where(Competitor.active == True)  # noqa: E712
        ).one(),
        "jobs_last_7d": len(job_rows),
        "events_last_7d": len(event_rows),
        "signals_count": len(signals),
        "urgent_count": severity_counts.get("urgent", 0),
        "signal_count": severity_counts.get("signal", 0),
        "opportunity_count": severity_counts.get("opportunity", 0),
    }

    tldr = _build_tldr(signals, market_trends, industry_pulse, finance)

    return {
        "week": week,
        "generated_date": datetime.utcnow().strftime("%d. %B %Y").replace("January", "januar").replace("February", "februar").replace("March", "marts").replace("April", "april").replace("May", "maj").replace("June", "juni").replace("July", "juli").replace("August", "august").replace("September", "september").replace("October", "oktober").replace("November", "november").replace("December", "december"),
        "top_tagline": signals[0]["title"] if signals else None,
        "tldr": tldr,
        "signals": signals,
        "market_trends": market_trends,
        "industry_pulse": industry_pulse,
        "finance": finance,
        "geo": geo,
        "top_skills": top_skills,
        "jobs_by_competitor": jobs_by_competitor,
        "recent_events": recent_events,
        "web_changes": web_changes,
        "stats": stats,
    }
