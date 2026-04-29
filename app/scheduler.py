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
from app.models import Competitor
from app.notifications import slack_alert
from app.analysis.classifier import classify_pending
from app.analysis.synthesizer import synthesize_week
from app.jobs.deliver_weekly import deliver
from app.scrapers.base import Scraper, ScrapeResult
from app.scrapers.career_sites import CareerSiteScraper
from app.scrapers.cvr import CvrScraper
from app.scrapers.google_news import GoogleNewsScraper
from app.scrapers.jobindex import JobindexScraper
from app.scrapers.wayback import WaybackScraper

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
    from sqlmodel import Session

    from app.db import engine

    logger.info("cron.synthesize.start")
    with Session(engine) as session:
        # Klassificer foerst, saa Sonnet faar bedst muligt input
        classify_pending(session)
        result = synthesize_week(session)
    logger.info("cron.synthesize.done", **result)
    if result.get("signals_added", 0) == 0:
        slack_alert(
            f"⚠️ Ugentlig syntese gav 0 signaler. Reason: {result.get('reason', 'ukendt')}"
        )


# Tidsplan jf. udviklingsplan sektion 06
SCHEDULE = [
    (JobindexScraper(), CronTrigger(hour=2, minute=0)),
    (CareerSiteScraper(), CronTrigger(hour=2, minute=30)),
    (CvrScraper(), CronTrigger(hour=3, minute=0)),
    (GoogleNewsScraper(), CronTrigger(hour=3, minute=30)),
    # Wayback koerer ugentligt soendag aften jf. plan sektion 06
    (WaybackScraper(), CronTrigger(day_of_week="sun", hour=21, minute=0)),
]


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(
        timezone="Europe/Copenhagen",
        job_defaults={"coalesce": True, "misfire_grace_time": 1800, "max_instances": 1},
    )
    for scraper, trigger in SCHEDULE:
        scheduler.add_job(
            _wrap(scraper),
            trigger=trigger,
            id=f"scrape_{scraper.source}",
            name=f"Scrape {scraper.source}",
            replace_existing=True,
        )
    # Haiku-klassificering dagligt 04:00 (efter de 4 daglige scrapere er faerdige)
    scheduler.add_job(
        _classify_job,
        trigger=CronTrigger(hour=4, minute=0),
        id="classify_pending",
        name="Klassificer nye jobopslag (Haiku)",
        replace_existing=True,
    )
    # Sonnet-syntese soendag 22:00 (efter wayback har koert)
    scheduler.add_job(
        _synthesize_job,
        trigger=CronTrigger(day_of_week="sun", hour=22, minute=0),
        id="synthesize_weekly",
        name="Ugentlig syntese (Sonnet)",
        replace_existing=True,
    )
    # Mandag 07:00 - byg + send PDF-rapport
    scheduler.add_job(
        _deliver_job,
        trigger=CronTrigger(day_of_week="mon", hour=7, minute=0),
        id="deliver_weekly",
        name="Send ugentlig PDF-rapport (Postmark)",
        replace_existing=True,
    )
    return scheduler


def _deliver_job() -> None:
    logger.info("cron.deliver.start")
    result = deliver()
    logger.info("cron.deliver.done", **result)
    if result.get("mail", {}).get("failed", 0) > 0:
        slack_alert(f"⚠️ Ugens rapport fejlede til {result['mail']['failed']} modtager(e)")
