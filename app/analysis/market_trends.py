"""Sonnet-baseret trend-analyse på det danske IT-marked.

Tager aggregerede MarketJobPosting-data over de seneste 12 uger og lader Claude
Sonnet 4.6 identificere 4-8 trends: growth, decline, emerging, spike, shift.
Resultaterne gemmes som MarketTrendSignal-rows.

Skip åbent hvis ANTHROPIC_API_KEY ikke er sat.
"""

from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from anthropic import Anthropic
from sqlmodel import Session, select

from app.models import MarketJobPosting, MarketTrendSignal

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "market_trends.md").read_text(encoding="utf-8")


def _client() -> Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Anthropic()


def _iso_week(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _aggregate_market(session: Session, weeks_back: int = 12) -> dict[str, Any]:
    """Aggregér klassificerede market-jobs over de seneste N uger."""
    since = datetime.utcnow() - timedelta(weeks=weeks_back)
    jobs = list(
        session.exec(
            select(MarketJobPosting).where(
                MarketJobPosting.first_seen_at >= since,
                MarketJobPosting.classified_at.is_not(None),  # type: ignore[union-attr]
            )
        ).all()
    )
    # Pr. uge: specialiseringer + tech_stack + total
    by_week_spec: dict[str, Counter] = defaultdict(Counter)
    by_week_tech: dict[str, Counter] = defaultdict(Counter)
    by_week_total: Counter = Counter()
    emerging_titles: list[dict[str, Any]] = []
    for j in jobs:
        week = _iso_week(j.first_seen_at)
        by_week_total[week] += 1
        if j.specialization:
            by_week_spec[week][j.specialization] += 1
        for tech in j.tech_stack or []:
            by_week_tech[week][str(tech)] += 1
        if j.is_emerging:
            emerging_titles.append(
                {"week": week, "title": j.title, "company": j.company, "spec": j.specialization}
            )
    weeks_sorted = sorted(by_week_total.keys())
    return {
        "weeks": weeks_sorted,
        "totals": {w: by_week_total[w] for w in weeks_sorted},
        "specializations": {w: dict(by_week_spec[w]) for w in weeks_sorted},
        "tech_stack": {w: dict(by_week_tech[w]) for w in weeks_sorted},
        "emerging_titles": emerging_titles[:30],  # max 30 i prompten
        "total_jobs_analyzed": len(jobs),
    }


def analyze_market_trends(session: Session) -> dict[str, Any]:
    """Hovedfunktion: aggregér, kald Sonnet, gem MarketTrendSignal-rows."""
    client = _client()
    if client is None:
        logger.warning("market_trends.skipped", reason="no_api_key")
        return {"signals_added": 0, "reason": "no_api_key"}

    aggregated = _aggregate_market(session)
    if aggregated["total_jobs_analyzed"] < 30:
        logger.info("market_trends.not_enough_data", total=aggregated["total_jobs_analyzed"])
        return {"signals_added": 0, "reason": "not_enough_classified_jobs", "total": aggregated["total_jobs_analyzed"]}

    week = _iso_week(datetime.utcnow())

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Aggregerede data fra DK IT-jobmarked (de sidste 12 uger):\n\n"
                    f"```json\n{json.dumps(aggregated, ensure_ascii=False, indent=2)}\n```"
                ),
            }
        ],
    )

    text = (response.content[0].text if response.content else "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        signals_data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.exception("market_trends.invalid_json", error=str(exc), raw=text[:500])
        return {"signals_added": 0, "reason": "invalid_json"}

    # Slet eksisterende signaler for samme uge for at undgå duplikater
    for old in session.exec(select(MarketTrendSignal).where(MarketTrendSignal.week == week)).all():
        session.delete(old)

    added = 0
    for entry in signals_data:
        try:
            session.add(
                MarketTrendSignal(
                    week=week,
                    signal_type=str(entry.get("signal_type", "growth"))[:50],
                    specialization=(str(entry.get("specialization")) if entry.get("specialization") else None),
                    tech=(str(entry.get("tech")) if entry.get("tech") else None),
                    severity=str(entry.get("severity", "signal"))[:20],
                    title=str(entry.get("title", ""))[:500],
                    summary=str(entry.get("summary", "")),
                    delta_pct=float(entry["delta_pct"]) if entry.get("delta_pct") is not None else None,
                    sample_size=int(entry["sample_size"]) if entry.get("sample_size") is not None else None,
                    recommended_action=entry.get("recommended_action"),
                    confidence=str(entry.get("confidence", "medium"))[:20],
                    source_refs={"aggregated_weeks": aggregated["weeks"], "total_jobs": aggregated["total_jobs_analyzed"]},
                )
            )
            added += 1
        except (ValueError, TypeError) as exc:
            logger.warning("market_trends.bad_entry", entry=entry, error=str(exc))

    session.commit()
    logger.info("market_trends.done", week=week, added=added, total_jobs=aggregated["total_jobs_analyzed"])
    return {"signals_added": added, "week": week, "total_jobs_analyzed": aggregated["total_jobs_analyzed"]}
