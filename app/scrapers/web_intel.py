"""Web-intel scraper.

To signaler i en scraper, kort fordi de begge stammer fra konkurrentens domaen:

1. **Tech stack** - hvilke teknologier bruger de? (CMS, ATS, analytics, marketing,
   chat-tooling, payment). Detekteres via response-headers, meta-tags, script-URLs,
   inline patterns. Foerste koersel = baseline, efterfoelgende = change-event hvis
   sajttet er aendret.

2. **Sitemap velocity** - antal URLs i /sitemap.xml. Spike i nye URLs = SEO/content-
   offensiv. Faldende = sitemap-rensning (sjaeldent men interessant).

Begge gemmes som CompanyEvent (source='web_intel') saa de viser sig i events-fanen
og indgaar i Sonnet-syntesen.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import httpx
import structlog
from bs4 import BeautifulSoup
from sqlmodel import Session, desc, select

from app.models import CompanyEvent, Competitor
from app.scrapers.base import ScrapeResult, Scraper

logger = structlog.get_logger(__name__)

HTTP_TIMEOUT = 25.0
USER_AGENT = "Mozilla/5.0 (compatible; konkurrenttracker/1.0; +https://epico.dk)"

# Mapping fra signal -> tech-label. Hver signal er enten en regex paa response-text,
# en header-key, eller en host-substring i en script-src.
TECH_PATTERNS: dict[str, dict[str, list[str]]] = {
    "ats": {
        "Greenhouse": [r"boards\.greenhouse\.io", r"greenhouse-job-board"],
        "Workday": [r"myworkdayjobs\.com", r"workday\.com"],
        "Lever": [r"jobs\.lever\.co", r"lever-jobs"],
        "SmartRecruiters": [r"smartrecruiters\.com"],
        "Teamtailor": [r"teamtailor\.com"],
        "HR-ON": [r"hr-on\.com", r"hr-on\.recruit"],
        "Emply": [r"emply\.com", r"emply\.net"],
        "Workable": [r"workable\.com"],
        "BambooHR": [r"bamboohr\.com"],
    },
    "cms": {
        "WordPress": [r"wp-content/", r"wp-includes/"],
        "Drupal": [r"sites/all/modules/", r"drupal\.js"],
        "HubSpot CMS": [r"hs-scripts\.com", r"hsforms\.net"],
        "Webflow": [r"webflow\.com"],
        "Sitecore": [r"sitecore"],
        "Umbraco": [r"umbraco"],
        "Contentful": [r"contentful\.com"],
    },
    "analytics": {
        "Google Analytics 4": [r"gtag/js", r"googletagmanager\.com/gtag"],
        "Google Tag Manager": [r"googletagmanager\.com/gtm"],
        "Adobe Analytics": [r"omtrdc\.net", r"adobedtm\.com"],
        "Plausible": [r"plausible\.io"],
        "Matomo": [r"matomo\.js", r"piwik\.js"],
        "Mixpanel": [r"mixpanel\.com"],
        "Segment": [r"segment\.com/analytics\.js", r"cdn\.segment"],
    },
    "marketing": {
        "HubSpot": [r"js\.hs-scripts\.com", r"hsforms"],
        "Marketo": [r"marketo\.com", r"munchkin\.js"],
        "Pardot": [r"pi\.pardot\.com"],
        "Mailchimp": [r"chimpstatic\.com", r"mailchimp\.com/embedded"],
        "ActiveCampaign": [r"activehosted\.com"],
        "Salesforce": [r"force\.com", r"salesforce\.com"],
    },
    "chat": {
        "Intercom": [r"intercom\.io", r"widget\.intercom"],
        "Drift": [r"js\.driftt\.com"],
        "Zendesk Chat": [r"zopim\.com", r"zdassets\.com"],
        "Crisp": [r"client\.crisp\.chat"],
        "LiveChat": [r"livechatinc\.com"],
    },
    "consent": {
        "Cookiebot": [r"consent\.cookiebot\.com"],
        "OneTrust": [r"onetrust\.com"],
        "Usercentrics": [r"usercentrics\.eu"],
    },
}


def _resolve_url(competitor: Competitor) -> str | None:
    config: dict[str, Any] = (competitor.scraper_config or {}).get("web_intel") or {}
    explicit = config.get("url")
    if explicit:
        return str(explicit)
    if competitor.domain:
        domain = competitor.domain.strip()
        if not domain.startswith("http"):
            return f"https://{domain}"
        return domain
    return None


def _detect_tech(html: str, headers: dict[str, str]) -> dict[str, list[str]]:
    """Returner mapping fra kategori -> liste af detekterede teknologier."""
    detected: dict[str, list[str]] = {}
    server = headers.get("server", "")
    powered_by = headers.get("x-powered-by", "")
    haystack = html + "\n" + server + "\n" + powered_by

    for category, technologies in TECH_PATTERNS.items():
        hits: list[str] = []
        for tech, patterns in technologies.items():
            for pattern in patterns:
                if re.search(pattern, haystack, re.IGNORECASE):
                    hits.append(tech)
                    break
        if hits:
            detected[category] = sorted(set(hits))
    # Server-headers
    if server and server.strip():
        detected.setdefault("server", []).append(server.split("/")[0].strip()[:50])
    return detected


def _diff_tech(before: dict[str, list[str]], after: dict[str, list[str]]) -> dict[str, dict[str, list[str]]]:
    """Find aendringer mellem to tech-snapshots."""
    changes: dict[str, dict[str, list[str]]] = {}
    categories = set(before) | set(after)
    for category in categories:
        prev_set = set(before.get(category, []))
        next_set = set(after.get(category, []))
        added = sorted(next_set - prev_set)
        removed = sorted(prev_set - next_set)
        if added or removed:
            changes[category] = {"added": added, "removed": removed}
    return changes


def _fetch_sitemap_url_count(base_url: str) -> int | None:
    """Tael antal URLs i /sitemap.xml. Returner None hvis ingen sitemap findes."""
    candidates = ["/sitemap.xml", "/sitemap_index.xml", "/sitemap-index.xml"]
    for path in candidates:
        try:
            response = httpx.get(
                base_url.rstrip("/") + path,
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            )
        except httpx.HTTPError:
            continue
        if response.status_code != 200 or not response.text.strip().startswith("<"):
            continue
        try:
            root = ET.fromstring(response.text)
        except ET.ParseError:
            continue
        # Tael loc'er - virker for baade <urlset> og <sitemapindex>
        ns = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
        urls = root.findall(f".//{ns}loc") or root.findall(".//loc")
        count = len(urls)
        if count > 0:
            return count
    return None


class WebIntelScraper(Scraper):
    source = "web_intel"

    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        result = ScrapeResult(source=self.source, competitor_slug=competitor.slug)
        url = _resolve_url(competitor)
        if not url:
            result.raw_warnings.append("ingen domain konfigureret - sprunget over")
            return result

        try:
            response = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=HTTP_TIMEOUT,
                follow_redirects=True,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            result.error = f"http_error: {exc}"
            return result

        result.items_seen = 1
        html = response.text
        headers = {k.lower(): v for k, v in response.headers.items()}
        # Inkluder script src'er saa eksterne tags ogsaa fanges
        soup = BeautifulSoup(html, "lxml")
        script_srcs = " ".join(s.get("src", "") for s in soup.find_all("script") if s.get("src"))
        link_hrefs = " ".join(l.get("href", "") for l in soup.find_all("link") if l.get("href"))
        scan_text = html + "\n" + script_srcs + "\n" + link_hrefs
        tech = _detect_tech(scan_text, headers)
        sitemap_count = _fetch_sitemap_url_count(url)

        now = datetime.utcnow()

        latest_tech = session.exec(
            select(CompanyEvent)
            .where(
                CompanyEvent.competitor_id == competitor.id,
                CompanyEvent.source == self.source,
                CompanyEvent.event_type.in_(["tech_baseline", "tech_change"]),  # type: ignore[union-attr]
            )
            .order_by(desc(CompanyEvent.detected_at))
        ).first()

        if latest_tech is None:
            tech_summary = ", ".join(
                f"{cat}: {'/'.join(items)}" for cat, items in sorted(tech.items()) if items
            )
            session.add(
                CompanyEvent(
                    competitor_id=competitor.id,  # type: ignore[arg-type]
                    event_type="tech_baseline",
                    source=self.source,
                    title=f"Tech-baseline: {url}",
                    description=tech_summary[:1000] or "Ingen kendte teknologier detekteret.",
                    url=url,
                    raw_data={"tech": tech, "sitemap_url_count": sitemap_count},
                    detected_at=now,
                )
            )
            result.items_added += 1
        else:
            previous_tech = (latest_tech.raw_data or {}).get("tech", {})
            changes = _diff_tech(previous_tech, tech)
            if changes:
                summary_parts: list[str] = []
                for category, delta in sorted(changes.items()):
                    parts: list[str] = []
                    if delta["added"]:
                        parts.append(f"+{', '.join(delta['added'])}")
                    if delta["removed"]:
                        parts.append(f"-{', '.join(delta['removed'])}")
                    summary_parts.append(f"{category}: {' '.join(parts)}")
                session.add(
                    CompanyEvent(
                        competitor_id=competitor.id,  # type: ignore[arg-type]
                        event_type="tech_change",
                        source=self.source,
                        title=f"Tech-aendring: {url}",
                        description=" | ".join(summary_parts)[:1000],
                        url=url,
                        raw_data={"tech": tech, "changes": changes, "previous": previous_tech},
                        detected_at=now,
                    )
                )
                result.items_added += 1

        # Sitemap-velocity: kun hvis sitemap findes
        if sitemap_count is not None:
            latest_sitemap = session.exec(
                select(CompanyEvent)
                .where(
                    CompanyEvent.competitor_id == competitor.id,
                    CompanyEvent.source == self.source,
                    CompanyEvent.event_type.in_(["sitemap_baseline", "sitemap_velocity"]),  # type: ignore[union-attr]
                )
                .order_by(desc(CompanyEvent.detected_at))
            ).first()

            if latest_sitemap is None:
                session.add(
                    CompanyEvent(
                        competitor_id=competitor.id,  # type: ignore[arg-type]
                        event_type="sitemap_baseline",
                        source=self.source,
                        title=f"Sitemap-baseline: {url}",
                        description=f"{sitemap_count} URLs i sitemap.",
                        url=url,
                        raw_data={"sitemap_url_count": sitemap_count},
                        detected_at=now,
                    )
                )
                result.items_added += 1
            else:
                previous_count = (latest_sitemap.raw_data or {}).get("sitemap_url_count")
                if isinstance(previous_count, int) and previous_count > 0:
                    delta = sitemap_count - previous_count
                    pct = abs(delta) / previous_count
                    # Trigger event hvis aendring >= 5% OG mindst 5 URLs forskel
                    if pct >= 0.05 and abs(delta) >= 5:
                        direction = "tilfoejet" if delta > 0 else "fjernet"
                        session.add(
                            CompanyEvent(
                                competitor_id=competitor.id,  # type: ignore[arg-type]
                                event_type="sitemap_velocity",
                                source=self.source,
                                title=f"Sitemap-aendring: {url}",
                                description=(
                                    f"{abs(delta)} URLs {direction} ({previous_count} -> {sitemap_count}, "
                                    f"{pct:.0%}). Indikator paa content-velocity."
                                ),
                                url=url,
                                raw_data={
                                    "sitemap_url_count": sitemap_count,
                                    "previous_count": previous_count,
                                    "delta": delta,
                                },
                                detected_at=now,
                            )
                        )
                        result.items_added += 1

        session.commit()
        logger.info(
            "scraper.run",
            source=self.source,
            slug=competitor.slug,
            tech_categories=list(tech.keys()),
            sitemap_url_count=sitemap_count,
            added=result.items_added,
        )
        return result
