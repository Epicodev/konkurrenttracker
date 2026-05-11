"""Sonnet-baseret 'industri-puls' - ugentlig syntese af industri-artikler.

Aggregerer klassificerede artikler over 7 dage og lader Claude Sonnet 4.6
identificere 3-5 dominerende temaer. Resultaterne gemmes som MarketTrendSignal-
rows med signal_type='industry_pulse'.
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

from app.models import IndustryArticle, MarketTrendSignal

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "industry_pulse.md").read_text(encoding="utf-8")


def _client() -> Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Anthropic()


def _iso_week(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _aggregate_week(session: Session, days_back: int = 7) -> dict[str, Any]:
    since = datetime.utcnow() - timedelta(days=days_back)
    articles = list(
        session.exec(
            select(IndustryArticle).where(
                IndustryArticle.first_seen_at >= since,
                IndustryArticle.is_classified.is_(True),  # type: ignore[union-attr]
            )
        ).all()
    )
    if not articles:
        return {"total": 0}

    by_topic: dict[str, list[dict[str, Any]]] = defaultdict(list)
    geo_counts: Counter = Counter()
    competitor_mentions: Counter = Counter()
    sources_counts: Counter = Counter()

    for a in articles:
        topic = a.topic or "other"
        by_topic[topic].append(
            {
                "title": a.title,
                "source": a.source,
                "geo": a.geo_scope,
                "url": a.url,
                "mentioned": a.mentioned_competitors or [],
            }
        )
        if a.geo_scope:
            geo_counts[a.geo_scope] += 1
        sources_counts[a.source] += 1
        for m in a.mentioned_competitors or []:
            competitor_mentions[m] += 1

    return {
        "total": len(articles),
        "by_topic": {
            topic: {
                "count": len(items),
                "sample_titles": [i["title"] for i in items[:8]],
                "sources": list({i["source"] for i in items}),
                "competitors_mentioned": list({m for i in items for m in i["mentioned"]}),
            }
            for topic, items in by_topic.items()
        },
        "geo_distribution": dict(geo_counts),
        "competitor_mentions": dict(competitor_mentions.most_common(15)),
        "source_distribution": dict(sources_counts),
    }


def generate_industry_pulse(session: Session) -> dict[str, Any]:
    """Hovedfunktion: aggregér, kald Sonnet, gem MarketTrendSignal-rows."""
    client = _client()
    if client is None:
        logger.warning("industry_pulse.skipped", reason="no_api_key")
        return {"signals_added": 0, "reason": "no_api_key"}

    aggregated = _aggregate_week(session)
    if aggregated["total"] < 10:
        logger.info("industry_pulse.not_enough_data", total=aggregated["total"])
        return {"signals_added": 0, "reason": "not_enough_articles", "total": aggregated["total"]}

    week = _iso_week(datetime.utcnow())

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=3500,
        system=[
            {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Ugens aggregerede industri-presse-data:\n\n"
                    f"```json\n{json.dumps(aggregated, ensure_ascii=False, indent=2)}\n```"
                ),
            }
        ],
    )

    text = (response.content[0].text if response.content else "").strip()
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        themes = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.exception("industry_pulse.invalid_json", error=str(exc), raw=text[:500])
        return {"signals_added": 0, "reason": "invalid_json"}

    # Slet eksisterende industry_pulse-signaler for samme uge (idempotent re-run)
    existing = session.exec(
        select(MarketTrendSignal).where(
            MarketTrendSignal.week == week,
            MarketTrendSignal.signal_type == "industry_pulse",
        )
    ).all()
    for row in existing:
        session.delete(row)

    added = 0
    for theme in themes:
        try:
            session.add(
                MarketTrendSignal(
                    week=week,
                    signal_type="industry_pulse",
                    specialization=str(theme.get("topic", ""))[:100] or None,
                    severity=str(theme.get("severity", "signal"))[:20],
                    title=str(theme.get("title", ""))[:500],
                    summary=str(theme.get("summary", "")),
                    sample_size=int(theme["sample_size"]) if theme.get("sample_size") is not None else None,
                    recommended_action=theme.get("recommended_action"),
                    confidence=str(theme.get("confidence", "medium"))[:20],
                    source_refs={
                        "geo_scope": theme.get("geo_scope"),
                        "mentioned_competitors": theme.get("mentioned_competitors", []),
                        "recommended_owner": theme.get("recommended_owner"),
                        "total_articles_analyzed": aggregated["total"],
                    },
                )
            )
            added += 1
        except (ValueError, TypeError) as exc:
            logger.warning("industry_pulse.bad_theme", theme=theme, error=str(exc))

    session.commit()
    logger.info("industry_pulse.done", week=week, themes=added, total_articles=aggregated["total"])
    return {"signals_added": added, "week": week, "total_articles": aggregated["total"]}
