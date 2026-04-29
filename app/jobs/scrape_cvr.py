"""Cron-entrypoint: koer CVR-scraperen for alle aktive konkurrenter med CVR-nummer.

Koeres som: python -m app.jobs.scrape_cvr
"""

import sys

from sqlmodel import Session, select

from app.db import engine
from app.models import Competitor
from app.scrapers.cvr import CvrScraper


def main() -> int:
    scraper = CvrScraper()
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
        skipped = "(skipped: no CVR)" if result.raw_warnings and not competitor.cvr else ""
        print(
            f"[cvr] {competitor.slug:20s} added={result.items_added} {skipped}"
            + (f" ERROR: {result.error}" if result.error else "")
        )

    print(f"\n[cvr] Total: {len(competitors)} konkurrenter, {total_added} nye events.")
    if failures:
        print(f"[cvr] {len(failures)} fejlede:", *failures, sep="\n  - ")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
