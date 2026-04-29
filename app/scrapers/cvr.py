"""CVR-scraper.

Henter virksomhedsdata fra cvrapi.dk for hver konkurrent med et CVR-nummer.
Detekterer aendringer ved at sammenligne med seneste cvr-event for samme konkurrent.

Foerste koersel pr. konkurrent = "cvr_baseline". Efterfoelgende = "cvr_change" hvis
relevante felter er aendret, ellers ingen ny event (saa tabellen ikke fyldes med ikke-events).
"""

from datetime import datetime
from typing import Any

import httpx
import structlog
from sqlmodel import Session, desc, select

from app.models import CompanyEvent, Competitor
from app.scrapers.base import ScrapeResult, Scraper

logger = structlog.get_logger(__name__)

API_URL = "https://cvrapi.dk/api"
HTTP_TIMEOUT = 20.0
USER_AGENT = "konkurrenttracker (epico.dk)"  # cvrapi.dk kraever User-Agent

# Felter vi sammenligner mellem koerseler. Aendringer i andre felter ignoreres.
TRACKED_FIELDS = (
    "name",
    "address",
    "zipcode",
    "city",
    "employees",
    "industrycode",
    "industrydesc",
    "companycode",
    "companydesc",
    "enddate",
    "creditbankrupt",
    "creditstatus",
)


def _fetch(cvr: str) -> dict[str, Any]:
    response = httpx.get(
        API_URL,
        params={"country": "dk", "vat": cvr},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


def _diff(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, dict[str, Any]]:
    changes: dict[str, dict[str, Any]] = {}
    for field in TRACKED_FIELDS:
        before = previous.get(field)
        after = current.get(field)
        if before != after:
            changes[field] = {"before": before, "after": after}
    return changes


class CvrScraper(Scraper):
    source = "cvr"

    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        result = ScrapeResult(source=self.source, competitor_slug=competitor.slug)

        if not competitor.cvr:
            result.raw_warnings.append("ingen CVR konfigureret - sprunget over")
            return result

        data = _fetch(competitor.cvr)
        result.items_seen = 1
        now = datetime.utcnow()

        latest_event = session.exec(
            select(CompanyEvent)
            .where(CompanyEvent.competitor_id == competitor.id, CompanyEvent.source == self.source)
            .order_by(desc(CompanyEvent.detected_at))
        ).first()

        if latest_event is None:
            session.add(
                CompanyEvent(
                    competitor_id=competitor.id,  # type: ignore[arg-type]
                    event_type="cvr_baseline",
                    source=self.source,
                    title=f"CVR-baseline: {data.get('name', competitor.name)}",
                    description=f"Foerste registrering. {data.get('employees') or '?'} ansatte, "
                    f"{data.get('industrydesc', 'ukendt branche')}.",
                    raw_data=data,
                    detected_at=now,
                )
            )
            session.commit()
            result.items_added = 1
            logger.info("scraper.run", source=self.source, slug=competitor.slug, type="baseline")
            return result

        changes = _diff(latest_event.raw_data, data)
        if not changes:
            logger.info("scraper.run", source=self.source, slug=competitor.slug, type="no_change")
            return result

        change_summary = ", ".join(f"{f}: {c['before']!r}->{c['after']!r}" for f, c in changes.items())
        session.add(
            CompanyEvent(
                competitor_id=competitor.id,  # type: ignore[arg-type]
                event_type="cvr_change",
                source=self.source,
                title=f"CVR-aendring: {data.get('name', competitor.name)}",
                description=change_summary[:1000],
                raw_data={"current": data, "changes": changes},
                detected_at=now,
            )
        )
        session.commit()
        result.items_added = 1
        logger.info(
            "scraper.run",
            source=self.source,
            slug=competitor.slug,
            type="change",
            changed_fields=list(changes.keys()),
        )
        return result
