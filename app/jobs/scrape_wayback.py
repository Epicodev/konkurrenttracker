"""Cron-entrypoint: koer wayback (web-snapshot) scraperen for alle aktive konkurrenter.

Koeres som: python -m app.jobs.scrape_wayback
"""

import sys

from sqlmodel import Session, select

from app.db import engine
from app.models import Competitor
from app.scrapers.wayback import WaybackScraper


def main() -> int:
    scraper = WaybackScraper()
    failures: list[str] = []
    total_added = 0

    with Session(engine) as session:
        competitors = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712

    for competitor in competitors:
        with Session(engine) as session:
            session.add(competitor)
            result = scraper.safe_scrape(competitor, session)
        total_added += result.items_added
        if result.error:
            failures.append(f"{competitor.slug}: {result.error}")
        warnings = ", ".join(result.raw_warnings) if result.raw_warnings else ""
        print(
            f"[wayback] {competitor.slug:20s} added={result.items_added}"
            + (f" WARN: {warnings}" if warnings else "")
            + (f" ERROR: {result.error}" if result.error else "")
        )

    print(f"\n[wayback] Total: {len(competitors)} konkurrenter, {total_added} nye events.")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
