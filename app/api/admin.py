"""Admin-endpoints til drift og debugging.

Bemærk: ingen auth endnu - kommer i Sprint 04 (HTTP Basic Auth).
"""

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, func, select

from app.analysis.classifier import classify_pending
from app.analysis.geo_tracker import run_geo_pass
from app.analysis.synthesizer import synthesize_week
from app.auth import require_basic_auth
from app.db import get_session
from app.jobs.deliver_weekly import deliver
from app.models import CompanyEvent, Competitor, JobPosting, Report, Signal
from app.reporting.builder import build_payload
from app.reporting.pdf import render_html
from app.scrapers.career_sites import CareerSiteScraper
from app.scrapers.cvr import CvrScraper
from app.scrapers.finance import FinanceScraper
from app.scrapers.google_news import GoogleNewsScraper
from app.scrapers.jobindex import JobindexScraper
from app.scrapers.wayback import WaybackScraper
from app.scrapers.web_intel import WebIntelScraper

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_basic_auth)])


class CompetitorUpsert(BaseModel):
    """Felter der kan opdateres via admin-UI. Alle valgfri - kun de udfyldte felter ændres."""

    name: str | None = None
    cvr: str | None = None
    domain: str | None = None
    career_url: str | None = None
    active: bool | None = None
    jobindex_query: str | None = None  # genvej til scraper_config['jobindex']['query']
    google_news_query: str | None = None
    geo_aliases: str | None = None  # komma-separeret liste


def _competitor_to_dict(c: Competitor) -> dict[str, Any]:
    config = c.scraper_config or {}
    aliases = (config.get("geo") or {}).get("aliases") or []
    return {
        "id": c.id,
        "slug": c.slug,
        "name": c.name,
        "cvr": c.cvr,
        "domain": c.domain,
        "career_url": c.career_url,
        "active": c.active,
        "jobindex_query": (config.get("jobindex") or {}).get("query"),
        "google_news_query": (config.get("google_news") or {}).get("query"),
        "geo_aliases": ", ".join(str(a) for a in aliases),
    }


@router.get("/competitors")
def admin_list_competitors(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Liste alle konkurrenter (også inaktive) med fulde konfig-felter til admin-UI."""
    rows = list(session.exec(select(Competitor).order_by(Competitor.id)).all())  # type: ignore[union-attr]
    return [_competitor_to_dict(c) for c in rows]


# Anbefalede defaults pr. slug. Bruges af /admin/competitors/fill-defaults til at
# populere tomme felter uden at overskrive brugerændringer.
COMPETITOR_DEFAULTS: dict[str, dict[str, Any]] = {
    "prodata": {
        "name": "ProData Consult A/S (nu emagine Consulting A/S)",
        "cvr": "26249627",
        "domain": "emagine.org",
        "jobindex_query": "ProData",
        "google_news_query": "ProData OR emagine Consulting",
        "geo_aliases": ["ProData", "emagine", "ProData Consult"],
    },
    "right-people": {
        "name": "Right People Group ApS",
        "cvr": "30590627",
        "domain": "rightpeoplegroup.com",
        "jobindex_query": "Right People",
        "google_news_query": "Right People Group",
        "geo_aliases": ["Right People", "Right People Group", "RPG"],
    },
    "hays": {
        "name": "Hays Specialist Recruitment Denmark A/S",
        "cvr": "30908848",
        "domain": "hays.dk",
        "jobindex_query": "Hays",
        "google_news_query": "Hays Denmark",
        "geo_aliases": ["Hays", "Hays Denmark", "Hays Specialist Recruitment"],
    },
    "zen": {
        "name": "Zen Consulting",
        "jobindex_query": "Zen Consulting",
        "google_news_query": "Zen Consulting",
        "geo_aliases": ["Zen Consulting", "Zen"],
    },
    "brainville": {
        "name": "Brainville",
        "jobindex_query": "Brainville",
        "google_news_query": "Brainville OR Ework Group",
        "geo_aliases": ["Brainville", "Ework", "Ework Group"],
    },
}


@router.post("/competitors/fill-defaults")
def admin_fill_defaults(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Fyld TOMME felter med anbefalede defaults. Overskriver IKKE eksisterende værdier."""
    updated_slugs: list[str] = []
    for slug, defaults in COMPETITOR_DEFAULTS.items():
        competitor = session.exec(select(Competitor).where(Competitor.slug == slug)).first()
        if competitor is None:
            continue
        changed = False
        # Top-level felter
        for field in ("name", "cvr", "domain", "career_url"):
            if field in defaults and not getattr(competitor, field):
                setattr(competitor, field, defaults[field])
                changed = True
        # scraper_config-felter (merge ind uden at slette andre keys)
        config = dict(competitor.scraper_config or {})
        if "jobindex_query" in defaults:
            jx = dict(config.get("jobindex") or {})
            if not jx.get("query"):
                jx["query"] = defaults["jobindex_query"]
                config["jobindex"] = jx
                changed = True
        if "google_news_query" in defaults:
            gn = dict(config.get("google_news") or {})
            if not gn.get("query"):
                gn["query"] = defaults["google_news_query"]
                config["google_news"] = gn
                changed = True
        if "geo_aliases" in defaults:
            geo = dict(config.get("geo") or {})
            if not geo.get("aliases"):
                geo["aliases"] = defaults["geo_aliases"]
                config["geo"] = geo
                changed = True
        if changed:
            competitor.scraper_config = config
            session.add(competitor)
            updated_slugs.append(slug)
    session.commit()
    return {"updated": updated_slugs, "count": len(updated_slugs)}


@router.patch("/competitors/{slug}")
def admin_update_competitor(
    slug: str,
    payload: CompetitorUpsert,
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Opdater enkelt konkurrent. Kun udfyldte felter ændres."""
    competitor = session.exec(select(Competitor).where(Competitor.slug == slug)).first()
    if competitor is None:
        raise HTTPException(status_code=404, detail=f"konkurrent '{slug}' ikke fundet")

    if payload.name is not None and payload.name.strip():
        competitor.name = payload.name.strip()[:200]
    if payload.cvr is not None:
        cleaned = "".join(ch for ch in payload.cvr if ch.isdigit())
        competitor.cvr = cleaned[:20] or None
    if payload.domain is not None:
        competitor.domain = (payload.domain.strip() or None) if payload.domain else None
        if competitor.domain:
            # Strip protocol/path for konsistens - bruges som "hays.dk"
            d = competitor.domain.replace("https://", "").replace("http://", "").rstrip("/")
            competitor.domain = d[:200]
    if payload.career_url is not None:
        competitor.career_url = (payload.career_url.strip() or None) if payload.career_url else None
        if competitor.career_url:
            competitor.career_url = competitor.career_url[:500]
    if payload.active is not None:
        competitor.active = payload.active

    # scraper_config flettes ind (queries + aliases) uden at smide andre felter væk
    config = dict(competitor.scraper_config or {})
    if payload.jobindex_query is not None:
        jobindex_cfg = dict(config.get("jobindex") or {})
        jobindex_cfg["query"] = payload.jobindex_query.strip() or None
        if jobindex_cfg["query"] is None:
            jobindex_cfg.pop("query", None)
        if jobindex_cfg:
            config["jobindex"] = jobindex_cfg
        else:
            config.pop("jobindex", None)
    if payload.google_news_query is not None:
        gn_cfg = dict(config.get("google_news") or {})
        gn_cfg["query"] = payload.google_news_query.strip() or None
        if gn_cfg["query"] is None:
            gn_cfg.pop("query", None)
        if gn_cfg:
            config["google_news"] = gn_cfg
        else:
            config.pop("google_news", None)
    if payload.geo_aliases is not None:
        aliases = [a.strip() for a in payload.geo_aliases.split(",") if a.strip()]
        geo_cfg = dict(config.get("geo") or {})
        if aliases:
            geo_cfg["aliases"] = aliases
            config["geo"] = geo_cfg
        else:
            geo_cfg.pop("aliases", None)
            if geo_cfg:
                config["geo"] = geo_cfg
            else:
                config.pop("geo", None)
    competitor.scraper_config = config

    session.add(competitor)
    session.commit()
    session.refresh(competitor)
    return _competitor_to_dict(competitor)


@router.get("/config-check")
def config_check() -> dict[str, Any]:
    """Vis hvilke env-vars der er sat. Bruges til at debugge Railway-deploys."""
    import os

    def status(var: str) -> str:
        val = os.environ.get(var)
        if not val:
            return "MISSING"
        if "KEY" in var or "TOKEN" in var or "PASSWORD" in var or "URL" in var:
            return f"SET (len={len(val)})"
        return f"SET ({val})"

    return {
        "database": {
            "DATABASE_URL": status("DATABASE_URL") if os.environ.get("DATABASE_URL") else "DEFAULT (sqlite)",
        },
        "auth": {
            "BASIC_AUTH_USER": status("BASIC_AUTH_USER"),
            "BASIC_AUTH_PASSWORD": status("BASIC_AUTH_PASSWORD"),
        },
        "anthropic": {"ANTHROPIC_API_KEY": status("ANTHROPIC_API_KEY")},
        "postmark": {
            "POSTMARK_SERVER_TOKEN": status("POSTMARK_SERVER_TOKEN"),
            "POSTMARK_FROM_EMAIL": status("POSTMARK_FROM_EMAIL"),
        },
        "slack": {"SLACK_WEBHOOK_URL": status("SLACK_WEBHOOK_URL")},
        "environment": status("ENVIRONMENT"),
    }


@router.get("/schedule")
def schedule_status() -> dict[str, Any]:
    """Lister alle scheduler-jobs og deres trigger-konfig."""
    from datetime import datetime

    from app.scheduler import JOB_CONFIGS

    now = datetime.now()
    jobs = [
        {
            "id": job_id,
            "name": name,
            "trigger": str(trigger),
            "next_run_local": str(trigger.get_next_fire_time(None, now)),
        }
        for job_id, name, _factory, trigger in JOB_CONFIGS
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
    """Manuel trigger - kører Jobindex-scraperen synkront for alle aktive konkurrenter."""
    return _run_scraper(JobindexScraper(), "jobindex", session)


@router.post("/scrape/cvr")
def trigger_cvr_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - kører CVR-scraperen synkront for alle aktive konkurrenter med CVR-nummer."""
    return _run_scraper(CvrScraper(), "cvr", session)


@router.post("/scrape/google_news")
def trigger_google_news_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - kører Google News-scraperen synkront for alle aktive konkurrenter."""
    return _run_scraper(GoogleNewsScraper(), "google_news", session)


@router.post("/scrape/career_sites")
def trigger_career_sites_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - kører karriere-side scraperen for alle aktive konkurrenter med career_url."""
    return _run_scraper(CareerSiteScraper(), "career_page", session)


@router.post("/scrape/wayback")
def trigger_wayback_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - kører wayback (web-snapshot) scraperen for alle aktive konkurrenter."""
    return _run_scraper(WaybackScraper(), "wayback", session)


@router.post("/scrape/web_intel")
def trigger_web_intel_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - tech stack + sitemap-velocity for alle aktive konkurrenter."""
    return _run_scraper(WebIntelScraper(), "web_intel", session)


@router.post("/scrape/finance")
def trigger_finance_scrape(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - regnskaber fra virk.dk distribution-API."""
    return _run_scraper(FinanceScraper(), "finance", session)


@router.post("/scrape/all")
def trigger_all_scrapers(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - kører alle 7 scrapere sekventielt. Bruges til ad-hoc rapport-trigger."""
    return {
        "jobindex": _run_scraper(JobindexScraper(), "jobindex", session),
        "cvr": _run_scraper(CvrScraper(), "cvr", session),
        "google_news": _run_scraper(GoogleNewsScraper(), "google_news", session),
        "career_sites": _run_scraper(CareerSiteScraper(), "career_page", session),
        "wayback": _run_scraper(WaybackScraper(), "wayback", session),
        "web_intel": _run_scraper(WebIntelScraper(), "web_intel", session),
        "finance": _run_scraper(FinanceScraper(), "finance", session),
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


@router.post("/analyze/geo")
def trigger_geo_pass(session: Session = Depends(get_session)) -> dict[str, Any]:
    """Manuel trigger - kør GEO share-of-voice måling mod Claude."""
    return run_geo_pass(session)


@router.post("/report/build")
def trigger_build_report(week: str | None = None) -> dict[str, Any]:
    """Manuel trigger - byg + send ugens rapport. ?week=2026-W17 for specifik uge."""
    return deliver(week=week)


@router.get("/report/preview", response_class=None)
def preview_report_html(session: Session = Depends(get_session), week: str | None = None) -> Any:
    """HTML-preview af rapporten uden PDF-rendering. Bruges til at iterere på template."""
    from fastapi.responses import HTMLResponse

    payload = build_payload(session, week=week)
    return HTMLResponse(content=render_html("weekly_report.html", payload))


@router.get("/report/download", response_class=None)
def download_report(session: Session = Depends(get_session), week: str | None = None) -> Any:
    """Generer PDF on-demand og returner som download.

    PDF'en regenereres altid fra nuværende DB-state - ingen persistent storage nødvendig.
    Opdaterer også Report-row med metadata så arkivet kan vise tidligere downloads.
    """
    from datetime import datetime

    from fastapi.responses import Response

    from app.reporting.pdf import render_pdf

    if week is None:
        iso_year, iso_week, _ = datetime.utcnow().isocalendar()
        week = f"{iso_year}-W{iso_week:02d}"

    payload = build_payload(session, week=week)
    pdf_bytes = render_pdf("weekly_report.html", payload)

    # Opdater eller opret Report-row med metadata (uden pdf_path - vi gemmer ikke til disk)
    existing = session.exec(select(Report).where(Report.week == week)).first()
    report = existing or Report(week=week)
    report.signal_count = len(payload["signals"])
    report.data_points = payload["stats"]["jobs_last_7d"] + payload["stats"]["events_last_7d"]
    report.exec_summary = payload.get("top_tagline")
    if not report.status or report.status == "pending":
        report.status = "generated"
    session.add(report)
    session.commit()

    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="epico-uge-{week}.pdf"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/reports")
def list_reports(session: Session = Depends(get_session)) -> list[dict[str, Any]]:
    """Liste over alle genererede rapporter."""
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
