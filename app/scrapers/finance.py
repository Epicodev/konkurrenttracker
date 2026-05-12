"""Finance-scraper.

Henter regnskaber fra Erhvervsstyrelsens distribution-API (offentlig, no-auth):
- POST distribution.virk.dk/offentliggoerelser/_search med CVR
- For hver hit: find XBRL-dokument, parse de 7 nøgletal
- Gem som FinancialReport-row + udsend CompanyEvent (event_type='annual_report')

XBRL-parsing: dansk regnskabstaksonomi bruger fsa:/gsd:-namespaces (Financial
Statements Authority). Vi matcher elementer på lokalnavn for at være robust
overfor taxonomy-versioner. Hver værdi har en contextRef der peger på et
xbrli:context med en regnskabsperiode - vi matcher på endDate == regnskabets
slutdato for at tage "næst-seneste" og ikke et sammenligningstal.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import httpx
import structlog
from lxml import etree
from sqlmodel import Session, select

from app.models import CompanyEvent, Competitor, FinancialReport
from app.scrapers.base import ScrapeResult, Scraper

logger = structlog.get_logger(__name__)

DISTRIBUTION_URL = "http://distribution.virk.dk/offentliggoerelser/_search"
HTTP_TIMEOUT = 30.0
USER_AGENT = "konkurrenttracker (epico.dk)"

# Map af KPI -> liste af mulige XBRL lokale element-navne (Danish FSA taxonomy varianter)
KPI_ELEMENTS: dict[str, list[str]] = {
    "revenue": ["Revenue", "NetTurnover", "GrossResultFromSalesOfGoodsAndServices"],
    "gross_profit": ["GrossProfitLoss", "GrossResult"],
    "profit_loss": ["ProfitLoss", "ProfitLossForThePeriod"],
    "employee_expenses": ["EmployeeBenefitsExpense", "WagesAndSalaries"],
    "equity": ["Equity"],
    "assets": ["Assets"],
    "average_employees": ["AverageNumberOfEmployees"],
}


def _search_publications(cvr: str, limit: int = 5) -> list[dict[str, Any]]:
    """Returner liste af regnskabs-offentliggoerelser for et CVR, nyeste først."""
    query = {
        "query": {"term": {"cvrNummer": cvr}},
        "sort": [{"offentliggoerelsesTidspunkt": "desc"}],
        "size": limit,
    }
    response = httpx.post(
        DISTRIBUTION_URL,
        json=query,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()
    return [hit.get("_source", {}) for hit in data.get("hits", {}).get("hits", [])]


def _pick_xbrl_url(publication: dict[str, Any]) -> str | None:
    for doc in publication.get("dokumenter", []) or []:
        mime = (doc.get("dokumentMimeType") or "").lower()
        if mime in ("application/xml", "text/xml"):
            return doc.get("dokumentUrl")
    return None


def _all_xbrl_urls(publication: dict[str, Any]) -> list[str]:
    """Returner ALLE XBRL-URLs - en publikation kan have flere XML-filer
    (fx koncernregnskab + parent-only). Vi parser dem alle og fletter resultater."""
    urls: list[str] = []
    for doc in publication.get("dokumenter", []) or []:
        mime = (doc.get("dokumentMimeType") or "").lower()
        url = doc.get("dokumentUrl")
        if mime in ("application/xml", "text/xml") and url:
            urls.append(url)
    return urls


def _pick_pdf_url(publication: dict[str, Any]) -> str | None:
    """Returner menneskeligt læsbart link til regnskabet.

    Prioritet:
    1. application/pdf - faktisk PDF (renderes i browser)
    2. application/xhtml+xml - iXBRL = HTML med embeddede XBRL-tags, renderes som læsbart dokument
    3. Intet (kalderen falder tilbage til rå XBRL)
    """
    docs = publication.get("dokumenter", []) or []
    for doc in docs:
        if (doc.get("dokumentMimeType") or "").lower() == "application/pdf":
            return doc.get("dokumentUrl")
    for doc in docs:
        if (doc.get("dokumentMimeType") or "").lower() == "application/xhtml+xml":
            return doc.get("dokumentUrl")
    return None


def _period_dates(publication: dict[str, Any]) -> tuple[date | None, date | None]:
    regnskab = publication.get("regnskab", {}) or {}
    period = regnskab.get("regnskabsperiode", {}) or {}
    start = period.get("startDato")
    end = period.get("slutDato")

    def to_date(value: Any) -> date | None:
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value)[:10]).date()
        except ValueError:
            return None

    return to_date(start), to_date(end)


def _local(tag: Any) -> str:
    """Strip namespace fra XML-tag - '{ns}Name' -> 'Name'.

    lxml giver Comment/PI-elementer en non-string tag (Cython callable).
    Vi returnerer tom streng for dem så iteration kan ignorere dem trygt.
    """
    if not isinstance(tag, str):
        return ""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_contexts(root: etree._Element) -> dict[str, dict[str, Any]]:
    """Returner mapping contextRef -> {start, end, instant}."""
    contexts: dict[str, dict[str, Any]] = {}
    for ctx in root.iter():
        if _local(ctx.tag) != "context":
            continue
        ctx_id = ctx.get("id")
        if not ctx_id:
            continue
        info: dict[str, Any] = {}
        for child in ctx.iter():
            local = _local(child.tag)
            text = (child.text or "").strip()
            if local == "startDate":
                info["start"] = text
            elif local == "endDate":
                info["end"] = text
            elif local == "instant":
                info["instant"] = text
        contexts[ctx_id] = info
    return contexts


def _match_context(
    contexts: dict[str, dict[str, Any]],
    fiscal_start: date | None,
    fiscal_end: date | None,
) -> set[str]:
    """Find context-ids der matcher regnskabsperioden (slutdato + evt. startdato)."""
    matching: set[str] = set()
    end_str = fiscal_end.isoformat() if fiscal_end else None
    start_str = fiscal_start.isoformat() if fiscal_start else None
    for ctx_id, info in contexts.items():
        # Duration-context: match start+end hvis begge kendes, ellers bare end
        if info.get("end") == end_str:
            if start_str is None or info.get("start") == start_str:
                matching.add(ctx_id)
        # Instant-context: match endDate
        if info.get("instant") == end_str:
            matching.add(ctx_id)
    return matching


def _extract_value(
    root: etree._Element,
    local_names: list[str],
    matching_contexts: set[str],
) -> float | None:
    """Find første værdi for nogen af local_names i en matchende context."""
    for elem in root.iter():
        if _local(elem.tag) not in local_names:
            continue
        ctx_ref = elem.get("contextRef")
        if ctx_ref and ctx_ref in matching_contexts:
            text = (elem.text or "").strip()
            if not text:
                continue
            try:
                return float(text)
            except ValueError:
                continue
    return None


def _parse_xbrl(xbrl_bytes: bytes, fiscal_start: date | None, fiscal_end: date | None) -> dict[str, float | None]:
    """Returner {revenue, gross_profit, profit_loss, ...} for den givne regnskabsperiode."""
    try:
        root = etree.fromstring(xbrl_bytes)
    except etree.XMLSyntaxError as exc:
        logger.warning("finance.xbrl_parse_error", error=str(exc))
        return {key: None for key in KPI_ELEMENTS}

    contexts = _parse_contexts(root)
    matching = _match_context(contexts, fiscal_start, fiscal_end)
    if not matching:
        # Fallback: hvis ingen match, brug alle "duration"-contexts (sjældent)
        logger.info("finance.no_matching_context", fiscal_end=str(fiscal_end))
        return {key: None for key in KPI_ELEMENTS}

    result: dict[str, float | None] = {}
    for kpi, names in KPI_ELEMENTS.items():
        result[kpi] = _extract_value(root, names, matching)
    return result


class FinanceScraper(Scraper):
    source = "finance"

    def scrape(self, competitor: Competitor, session: Session) -> ScrapeResult:
        result = ScrapeResult(source=self.source, competitor_slug=competitor.slug)
        if not competitor.cvr:
            result.raw_warnings.append("ingen CVR - sprunget over")
            return result

        try:
            publications = _search_publications(competitor.cvr)
        except httpx.HTTPError as exc:
            result.error = f"distribution_api: {exc}"
            return result

        result.items_seen = len(publications)
        existing_rows: dict[date, FinancialReport] = {
            r.fiscal_year_end: r
            for r in session.exec(
                select(FinancialReport).where(FinancialReport.competitor_id == competitor.id)
            ).all()
        }

        for pub in publications:
            fiscal_start, fiscal_end = _period_dates(pub)
            if fiscal_end is None:
                continue

            xbrl_urls = _all_xbrl_urls(pub)
            pdf_url = _pick_pdf_url(pub)
            # Parse ALLE XBRL-filer i publikationen og flet noegletallene.
            # En publikation kan have baade parent-only og koncernregnskab; koncern har de
            # fyldigste tal mens parent-only ofte kun har antal ansatte.
            kpis: dict[str, float | None] = {key: None for key in KPI_ELEMENTS}
            used_xbrl_url: str | None = None
            for url in xbrl_urls:
                try:
                    resp = httpx.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
                    resp.raise_for_status()
                except httpx.HTTPError as exc:
                    logger.warning("finance.xbrl_fetch_failed", url=url, error=str(exc))
                    continue
                parsed = _parse_xbrl(resp.content, fiscal_start, fiscal_end)
                # Flet ind: kun udfyld felter der mangler
                for key, value in parsed.items():
                    if kpis.get(key) is None and value is not None:
                        kpis[key] = value
                        used_xbrl_url = url

            published_at = None
            published_raw = pub.get("offentliggoerelsesTidspunkt")
            if published_raw:
                try:
                    published_at = datetime.fromisoformat(str(published_raw).replace("Z", "+00:00")).replace(tzinfo=None)
                except (ValueError, TypeError):
                    pass

            avg_emp = kpis.get("average_employees")
            avg_emp_int = int(avg_emp) if isinstance(avg_emp, (int, float)) else None

            existing = existing_rows.get(fiscal_end)
            if existing is not None:
                # Backfill: udfyld kun felter der mangler, overskriv aldrig faktiske vaerdier.
                # Bruges naar vi har en sparse row fra tidligere koersel og parser nu finder fyldigere data.
                changed = False
                for field, value in (
                    ("revenue", kpis.get("revenue")),
                    ("gross_profit", kpis.get("gross_profit")),
                    ("profit_loss", kpis.get("profit_loss")),
                    ("employee_expenses", kpis.get("employee_expenses")),
                    ("equity", kpis.get("equity")),
                    ("assets", kpis.get("assets")),
                    ("average_employees", avg_emp_int),
                ):
                    if getattr(existing, field) is None and value is not None:
                        setattr(existing, field, value)
                        changed = True
                if not existing.pdf_url and pdf_url:
                    existing.pdf_url = pdf_url; changed = True
                if not existing.xbrl_url and used_xbrl_url:
                    existing.xbrl_url = used_xbrl_url; changed = True
                if not existing.fiscal_year_start and fiscal_start:
                    existing.fiscal_year_start = fiscal_start; changed = True
                if changed:
                    session.add(existing)
                    result.items_added += 1
                # Ingen ny CompanyEvent ved backfill - kun ved foerste-gangs indsaettelse
                continue

            report = FinancialReport(
                competitor_id=competitor.id,  # type: ignore[arg-type]
                fiscal_year_start=fiscal_start,
                fiscal_year_end=fiscal_end,
                revenue=kpis.get("revenue"),
                gross_profit=kpis.get("gross_profit"),
                profit_loss=kpis.get("profit_loss"),
                employee_expenses=kpis.get("employee_expenses"),
                equity=kpis.get("equity"),
                assets=kpis.get("assets"),
                average_employees=avg_emp_int,
                pdf_url=pdf_url,
                xbrl_url=used_xbrl_url,
                published_at=published_at,
            )
            session.add(report)
            existing_rows[fiscal_end] = report

            # Lav en human-readable opsummering til CompanyEvent
            def _fmt_dkk(value: float | None) -> str:
                if value is None:
                    return "?"
                if abs(value) >= 1_000_000:
                    return f"{value / 1_000_000:.1f} mio kr"
                if abs(value) >= 1_000:
                    return f"{value / 1_000:.0f} tkr"
                return f"{value:.0f} kr"

            summary = (
                f"Regnskab {fiscal_end.year} ({fiscal_end.isoformat()}). "
                f"Omsætning: {_fmt_dkk(kpis.get('revenue'))}, "
                f"resultat: {_fmt_dkk(kpis.get('profit_loss'))}, "
                f"egenkapital: {_fmt_dkk(kpis.get('equity'))}, "
                f"ansatte: {avg_emp_int if avg_emp_int else '?'}."
            )
            session.add(
                CompanyEvent(
                    competitor_id=competitor.id,  # type: ignore[arg-type]
                    event_type="annual_report",
                    source=self.source,
                    external_id=f"{competitor.cvr}-{fiscal_end.isoformat()}"[:500],
                    title=f"Nyt regnskab: {competitor.name} ({fiscal_end.year})",
                    description=summary,
                    url=pdf_url,
                    raw_data={"kpis": kpis, "xbrl_url": xbrl_url, "pdf_url": pdf_url},
                    occurred_at=published_at,
                    detected_at=datetime.utcnow(),
                )
            )
            result.items_added += 1

        session.commit()
        logger.info(
            "scraper.run",
            source=self.source,
            slug=competitor.slug,
            cvr=competitor.cvr,
            seen=result.items_seen,
            added=result.items_added,
        )
        return result
