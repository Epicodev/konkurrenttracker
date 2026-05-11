from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import structlog
from sqlmodel import Session

from app.models import Competitor

logger = structlog.get_logger(__name__)


@dataclass
class ScrapeResult:
    source: str
    competitor_slug: str
    items_seen: int = 0
    items_added: int = 0
    error: str | None = None
    raw_warnings: list[str] = field(default_factory=list)


class Scraper(ABC):
    """Fælles interface for alle scrapere.

    Hver scraper kører mod EN konkurrent ad gangen og er ansvarlig for at:
    - Hente raw data fra sin kilde
    - Persistere nye rows (idempotent via unique constraints i modellerne)
    - Returnere et ScrapeResult med tællinger og evt. fejl
    """

    source: str  # fx "jobindex", "cvr", "google_news"

    @abstractmethod
    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        """Scrape EN konkurrent. Skal være idempotent (genkørsel = ingen duplikater)."""

    def safe_scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        """Wrapper der fanger exceptions så en dårlig konkurrent ikke stækker hele cron-jobbet."""
        try:
            return self.scrape(competitor, session)
        except Exception as exc:  # noqa: BLE001
            logger.exception("scraper.failed", source=self.source, slug=competitor.slug)
            return ScrapeResult(
                source=self.source,
                competitor_slug=competitor.slug,
                error=f"{type(exc).__name__}: {exc}",
            )


def jobindex_query_for(competitor: Competitor) -> str | None:
    """Returner eksplicit query fra scraper_config['jobindex']['query'], eller None hvis ikke konfigureret.

    Vi falder IKKE tilbage til competitor.name fordi Jobindex laver OR-match på multiword queries,
    hvilket giver fallback-resultater for placeholders. Bedre at springe over end at gemme støj.
    """
    config: dict[str, Any] = competitor.scraper_config or {}
    explicit = config.get("jobindex", {}).get("query")
    return str(explicit) if explicit else None
