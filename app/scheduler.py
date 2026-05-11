"""In-process cron-scheduler.

APScheduler koerer paa FastAPI-processen og afvikler de 5 scrapere efter samme
tidsplan som udviklingsplanen specificerer. Misfire grace time tillader at jobs
afvikles efter container-restart hvis de er taet paa missing.
"""

from __future__ import annotations

import structlog
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from app.db import engine
from app.notifications import slack_alert
from app.analysis.classifier import classify_pending
from app.analysis.geo_tracker import run_geo_pass
from app.analysis.synthesizer import synthesize_week
from app.jobs.deliver_weekly import deliver
from app.models import Competitor, Signal
from app.scrapers.base import Scraper, ScrapeResult
from app.scrapers.career_sites import CareerSiteScraper
from app.scrapers.cvr import CvrScraper
from app.scrapers.finance import FinanceScraper
from app.scrapers.google_news import GoogleNewsScraper
from app.scrapers.jobindex import JobindexScraper
from app.scrapers.wayback import WaybackScraper
from app.scrapers.web_intel import WebIntelScraper

logger = structlog.get_logger(__name__)


def _run_for_all(scraper: Scraper) -> tuple[int, int, list[ScrapeResult]]:
    """Run scraper for every active competitor. Returns (total_added, failed_count, results)."""
    with Session(engine) as session:
        competitors = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712

    results: list[ScrapeResult] = []
    for competitor in competitors:
        with Session(engine) as session:
            session.add(competitor)
            results.append(scraper.safe_scrape(competitor, session))

    total_added = sum(r.items_added for r in results)
    failed = sum(1 for r in results if r.error)
    return total_added, failed, results


def _wrap(scraper: Scraper) -> callable:  # type: ignore[type-arg]
    def job() -> None:
        logger.info("cron.start", source=scraper.source)
        added, failed, results = _run_for_all(scraper)
        logger.info("cron.done", source=scraper.source, added=added, failed=failed)
        if failed > 0:
            failures = [f"{r.competitor_slug}: {r.error}" for r in results if r.error]
            slack_alert(
                f"⚠️ Scraper *{scraper.source}* fejlede for {failed} konkurrent(er):\n"
                + "\n".join(failures[:5])
            )
        elif added == 0 and scraper.source in ("jobindex", "career_page"):
            # 0 nye er mistaenkeligt for de "skroebelige" scrapere
            slack_alert(f"ℹ️ Scraper *{scraper.source}* fandt 0 nye opslag i denne koersel.")

    job.__name__ = f"cron_{scraper.source}"
    return job


def _classify_job() -> None:
    from sqlmodel import Session

    from app.db import engine

    logger.info("cron.classify.start")
    with Session(engine) as session:
        result = classify_pending(session)
    logger.info("cron.classify.done", **result)


def _synthesize_job() -> None:
    logger.info("cron.synthesize.start")
    with Session(engine) as session:
        # Klassificer foerst, saa Sonnet faar bedst muligt input
        classify_pending(session)
        result = synthesize_week(session)
        # Find urgent-signaler i denne uge og post pr. signal til Slack
        if result.get("signals_added", 0) > 0:
            week = result.get("week")
            urgent_rows = list(
                session.exec(
                    select(Signal, Competitor)
                    .join(Competitor, Signal.competitor_id == Competitor.id)
                    .where(Signal.week == week, Signal.severity == "urgent")
                ).all()
            )
            for signal, competitor in urgent_rows:
                slack_alert(
                    f"🚨 *URGENT signal* - {competitor.name}\n"
                    f"*{signal.title}*\n"
                    f"{signal.summary[:400]}\n"
                    f"_Anbefaling:_ {signal.recommended_action or '-'} "
                    f"(_owner:_ {signal.recommended_owner or '-'})"
                )
    logger.info("cron.synthesize.done", **result)
    if result.get("signals_added", 0) == 0:
        slack_alert(
            f"⚠️ Ugentlig syntese gav 0 signaler. Reason: {result.get('reason', 'ukendt')}"
        )


def _geo_job() -> None:
    logger.info("cron.geo.start")
    with Session(engine) as session:
        result = run_geo_pass(session)
    logger.info("cron.geo.done", **result)
    if result.get("competitors_tracked", 0) == 0:
        slack_alert(f"⚠️ GEO-pass tilfoejede 0 maalinger. Reason: {result.get('reason', 'ukendt')}")


def _deliver_job() -> None:
    logger.info("cron.deliver.start")
    result = deliver()
    logger.info("cron.deliver.done", **result)
    if result.get("mail", {}).get("failed", 0) > 0:
        slack_alert(f"⚠️ Ugens rapport fejlede til {result['mail']['failed']} modtager(e)")


# Single source of truth for alle scheduler-jobs jf. udviklingsplan sektion 06.
# (id, name, callable_factory, trigger). callable_factory tager ingen args og returnerer
# det callable scheduler skal koere - vi bruger lambda saa scrapere kan instantieres lazy.
JOB_CONFIGS: list[tuple[str, str, callable, CronTrigger]] = [  # type: ignore[type-arg]
    ("scrape_jobindex", "Scrape Jobindex", lambda: _wrap(JobindexScraper()), CronTrigger(hour=2, minute=0)),
    ("scrape_career_page", "Scrape karriere-sider", lambda: _wrap(CareerSiteScraper()), CronTrigger(hour=2, minute=30)),
    ("scrape_cvr", "Scrape CVR", lambda: _wrap(CvrScraper()), CronTrigger(hour=3, minute=0)),
    ("scrape_google_news", "Scrape Google News", lambda: _wrap(GoogleNewsScraper()), CronTrigger(hour=3, minute=30)),
    ("classify_pending", "Klassificer nye jobopslag (Haiku)", lambda: _classify_job, CronTrigger(hour=4, minute=0)),
    ("scrape_wayback", "Scrape Wayback (web-snapshots)", lambda: _wrap(WaybackScraper()), CronTrigger(day_of_week="sun", hour=21, minute=0)),
    ("scrape_web_intel", "Scrape web-intel (tech stack + sitemap)", lambda: _wrap(WebIntelScraper()), CronTrigger(day_of_week="sun", hour=21, minute=20)),
    ("scrape_finance", "Scrape regnskaber (virk.dk distribution)", lambda: _wrap(FinanceScraper()), CronTrigger(day_of_week="sun", hour=21, minute=30)),
    ("geo_weekly", "GEO share-of-voice (Claude)", lambda: _geo_job, CronTrigger(day_of_week="sun", hour=21, minute=40)),
    ("synthesize_weekly", "Ugentlig syntese (Sonnet)", lambda: _synthesize_job, CronTrigger(day_of_week="sun", hour=22, minute=0)),
    ("deliver_weekly", "Send ugentlig PDF-rapport (Postmark)", lambda: _deliver_job, CronTrigger(day_of_week="mon", hour=7, minute=0)),
]


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        timezone="Europe/Copenhagen",
        job_defaults={"coalesce": True, "misfire_grace_time": 1800, "max_instances": 1},
    )
    for job_id, name, fn_factory, trigger in JOB_CONFIGS:
        scheduler.add_job(
            fn_factory(),
            trigger=trigger,
            id=job_id,
            name=name,
            replace_existing=True,
        )
    return scheduler
