"""Cron-entrypoint: koer Google News-scraperen for alle aktive konkurrenter med query.

Koeres som: python -m app.jobs.scrape_google_news
"""

import sys

from sqlmodel import Session, select

from app.db import engine
from app.models import Competitor
from app.scrapers.google_news import GoogleNewsScraper


def main() -> int:
    scraper = GoogleNewsScraper()
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
        print(
            f"[google_news] {competitor.slug:20s} seen={result.items_seen:>3} added={result.items_added:>3}"
            + (f" ERROR: {result.error}" if result.error else "")
        )

    print(f"\n[google_news] Total: {len(competitors)} konkurrenter, {total_added} nye events.")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
