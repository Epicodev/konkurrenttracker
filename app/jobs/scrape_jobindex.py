"""Cron-entrypoint: scrape Jobindex for alle aktive konkurrenter.

Koeres som: python -m app.jobs.scrape_jobindex
"""

import sys

import structlog
from sqlmodel import Session, select

from app.db import engine
from app.models import Competitor
from app.scrapers.jobindex import JobindexScraper

logger = structlog.get_logger(__name__)


def main() -> int:
    scraper = JobindexScraper()
    total_seen = 0
    total_added = 0
    failures: list[str] = []

    with Session(engine) as session:
        competitors = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712

    for competitor in competitors:
        with Session(engine) as session:
            session.add(competitor)
            result = scraper.safe_scrape(competitor, session)
        total_seen += result.items_seen
        total_added += result.items_added
        if result.error:
            failures.append(f"{competitor.slug}: {result.error}")
        print(
            f"[jobindex] {competitor.slug:20s} seen={result.items_seen:>3} added={result.items_added:>3}"
            + (f" ERROR: {result.error}" if result.error else "")
        )

    print(f"\n[jobindex] Total: {len(competitors)} konkurrenter, {total_seen} sete, {total_added} nye.")
    if failures:
        print(f"[jobindex] {len(failures)} fejlede:", *failures, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
