"""Web-snapshot scraper.

Tager et snapshot af konkurrentens forside og diff'er mod sidste snapshot for samme konkurrent.

Trods navnet ('wayback' i datakilde-listen) bruger vi *vores egne* snapshots gemt i
CompanyEvent.raw_data - ikke archive.org. Det er hurtigere, og selve diff-detektionen er pointen.
"""

import hashlib
import re
from datetime import datetime
from difflib import unified_diff
from typing import Any

import httpx
import structlog
from bs4 import BeautifulSoup
from sqlmodel import Session, desc, select

from app.models import CompanyEvent, Competitor
from app.scrapers.base import ScrapeResult, Scraper

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT = 30.0
USER_AGENT = "Mozilla/5.0 (compatible; konkurrenttracker/1.0; +https://epico.dk)"
# Min. aendret antal tegn foer vi gemmer en change-event - undgaa stoej fra timestamps/CSRF tokens
SIGNIFICANT_CHANGE_THRESHOLD = 200
SNAPSHOT_MAX_BYTES = 50_000  # gem max 50KB raw_data pr. snapshot


def _resolve_url(competitor: Competitor) -> str | None:
    config: dict[str, Any] = (competitor.scraper_config or {}).get("wayback") or {}
    explicit = config.get("url")
    if explicit:
        return str(explicit)
    if competitor.domain:
        domain = competitor.domain.strip()
        if not domain.startswith("http"):
            return f"https://{domain}"
        return domain
    return None


def _extract_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n", strip=True)
    # Normaliser whitespace - undgaa flueknepperi paa tomme linjer
    text = re.sub(r"\n{2,}", "\n", text)
    return text


def _summarize_diff(before: str, after: str) -> str:
    diff_lines = list(
        unified_diff(
            before.splitlines(),
            after.splitlines(),
            lineterm="",
            n=1,
        )
    )
    added = [line for line in diff_lines if line.startswith("+") and not line.startswith("+++")]
    removed = [line for line in diff_lines if line.startswith("-") and not line.startswith("---")]
    summary = (
        f"+{len(added)} linje(r) tilfoejet, -{len(removed)} fjernet. "
        f"Foerste tilfoejelser: {' | '.join(line[1:].strip() for line in added[:3])}"
    )
    return summary[:1000]


class WaybackScraper(Scraper):
    source = "wayback"

    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        result = ScrapeResult(source=self.source, competitor_slug=competitor.slug)
        url = _resolve_url(competitor)
        if not url:
            result.raw_warnings.append("ingen wayback-url eller domain konfigureret - sprunget over")
            return result

        response = httpx.get(
            url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, follow_redirects=True
        )
        response.raise_for_status()
        result.items_seen = 1

        current_text = _extract_text(response.text)
        current_hash = hashlib.sha256(current_text.encode("utf-8")).hexdigest()
        now = datetime.utcnow()

        latest = session.exec(
            select(CompanyEvent)
            .where(CompanyEvent.competitor_id == competitor.id, CompanyEvent.source == self.source)
            .order_by(desc(CompanyEvent.detected_at))
        ).first()

        if latest is None:
            session.add(
                CompanyEvent(
                    competitor_id=competitor.id,  # type: ignore[arg-type]
                    event_type="web_baseline",
                    source=self.source,
                    title=f"Web-baseline: {url}",
                    description=f"Foerste snapshot, {len(current_text)} tegn ren tekst.",
                    url=url,
                    raw_data={"hash": current_hash, "text": current_text[:SNAPSHOT_MAX_BYTES]},
                    detected_at=now,
                )
            )
            session.commit()
            result.items_added = 1
            logger.info("scraper.run", source=self.source, slug=competitor.slug, type="baseline")
            return result

        previous_hash = latest.raw_data.get("hash")
        if previous_hash == current_hash:
            logger.info("scraper.run", source=self.source, slug=competitor.slug, type="no_change")
            return result

        previous_text = latest.raw_data.get("text", "")
        delta = abs(len(current_text) - len(previous_text))
        if delta < SIGNIFICANT_CHANGE_THRESHOLD:
            logger.info(
                "scraper.run",
                source=self.source,
                slug=competitor.slug,
                type="below_threshold",
                delta=delta,
            )
            return result

        summary = _summarize_diff(previous_text, current_text)
        session.add(
            CompanyEvent(
                competitor_id=competitor.id,  # type: ignore[arg-type]
                event_type="web_change",
                source=self.source,
                title=f"Web-aendring: {url}",
                description=summary,
                url=url,
                raw_data={
                    "hash": current_hash,
                    "previous_hash": previous_hash,
                    "delta_chars": delta,
                    "text": current_text[:SNAPSHOT_MAX_BYTES],
                },
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
            delta=delta,
        )
        return result
