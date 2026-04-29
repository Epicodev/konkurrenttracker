"""Career-site-scraper.

Henter konkurrentens karriere-side (statisk HTML) og parser job-cards med BeautifulSoup.

Per-konkurrent override via competitor.scraper_config['career_site']:
    {
      "url": "https://example.com/careers",          # override af competitor.career_url
      "job_card_selector": "article.job-card",       # CSS-selector for hver post
      "title_selector": "h3.job-title",              # selector inde i job-card
      "link_selector": "a.apply",                    # selector for URL (valgfri)
      "location_selector": ".location"               # selector for sted (valgfri)
    }

Hvis ingen config: prover en raekke faelles fallback-selectors. Returnerer 0 hvis intet matcher
(forventet for SPA-sider der renderer i JavaScript - alert-mekanik kommer i Sprint 02 trin 5).
"""

import hashlib
from datetime import datetime
from typing import Any

import httpx
import structlog
from bs4 import BeautifulSoup
from sqlmodel import Session, select

from app.models import Competitor, JobPosting
from app.scrapers.base import ScrapeResult, Scraper

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (compatible; konkurrenttracker/1.0; +https://epico.dk)"

FALLBACK_JOB_CARD_SELECTORS = (
    "article.job",
    "article.job-card",
    "div.job-card",
    "li.job-listing",
    "[data-job-id]",
    "[class*='JobListing']",
    "[class*='job-tile']",
)


def _resolve_url(competitor: Competitor) -> str | None:
    config: dict[str, Any] = (competitor.scraper_config or {}).get("career_site") or {}
    return config.get("url") or competitor.career_url


def _config(competitor: Competitor) -> dict[str, Any]:
    return (competitor.scraper_config or {}).get("career_site") or {}


def _find_job_cards(soup: BeautifulSoup, selector: str | None) -> list[Any]:
    if selector:
        return list(soup.select(selector))
    for fallback in FALLBACK_JOB_CARD_SELECTORS:
        cards = soup.select(fallback)
        if cards:
            return list(cards)
    return []


def _extract(card: Any, selector: str | None, default: str = "") -> str:
    if selector:
        match = card.select_one(selector)
        return match.get_text(strip=True) if match else default
    # Fallback: try common patterns
    for tag in ("h2", "h3", "h4", "a"):
        match = card.find(tag)
        if match:
            return match.get_text(strip=True)
    return default


def _extract_link(card: Any, selector: str | None, base_url: str) -> str | None:
    candidate = card.select_one(selector) if selector else card.find("a", href=True)
    if not candidate:
        return None
    href = candidate.get("href") if hasattr(candidate, "get") else None
    if not href:
        return None
    if href.startswith("http"):
        return href
    if href.startswith("/"):
        from urllib.parse import urljoin

        return urljoin(base_url, href)
    return href


class CareerSiteScraper(Scraper):
    source = "career_page"

    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        result = ScrapeResult(source=self.source, competitor_slug=competitor.slug)
        url = _resolve_url(competitor)
        if not url:
            result.raw_warnings.append("ingen career_url konfigureret - sprunget over")
            return result

        config = _config(competitor)

        response = httpx.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, follow_redirects=True
        )
        response.raise_for_status()

        soup = BeautifulSoup(response.text, "lxml")
        cards = _find_job_cards(soup, config.get("job_card_selector"))

        if not cards:
            result.raw_warnings.append(f"ingen job-cards fundet paa {url} - SPA eller forkert selector?")
            return result

        existing_external_ids = set(
            session.exec(
                select(JobPosting.external_id).where(
                    JobPosting.competitor_id == competitor.id,
                    JobPosting.source == self.source,
                )
            ).all()
        )

        now = datetime.utcnow()
        for card in cards:
            result.items_seen += 1
            title = _extract(card, config.get("title_selector"))
            if not title:
                continue
            link = _extract_link(card, config.get("link_selector"), url)
            location = _extract(card, config.get("location_selector"), default="")

            # Bruger link som external_id, ellers hash af titel+placering+url
            if link:
                external_id = link
            else:
                key = f"{competitor.slug}|{title}|{location}|{url}"
                external_id = hashlib.sha256(key.encode("utf-8")).hexdigest()

            if external_id in existing_external_ids:
                continue

            session.add(
                JobPosting(
                    competitor_id=competitor.id,  # type: ignore[arg-type]
                    external_id=external_id[:500],
                    title=title[:500],
                    description=card.get_text(separator=" ", strip=True)[:5000] or None,
                    location=location[:200] if location else None,
                    source=self.source,
                    url=link,
                    raw_data={"html_snippet": str(card)[:2000]},
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
            existing_external_ids.add(external_id)
            result.items_added += 1

        session.commit()
        logger.info(
            "scraper.run",
            source=self.source,
            slug=competitor.slug,
            url=url,
            seen=result.items_seen,
            added=result.items_added,
        )
        return result
