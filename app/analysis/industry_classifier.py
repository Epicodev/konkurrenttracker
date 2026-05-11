"""Haiku-baseret klassificering af IndustryArticle-rows.

Beriger med topic, geo_scope og mentioned_competitors. System-prompten
indeholder dynamisk konkurrent-liste så Haiku kender slug-mapping.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from anthropic import Anthropic
from sqlmodel import Session, select

from app.models import Competitor, IndustryArticle

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-haiku-4-5"
PROMPT_PATH = Path(__file__).parent / "prompts" / "classify_industry_article.md"

EXPECTED_FIELDS = ("topic", "geo_scope", "mentioned_competitors")


def _client() -> Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Anthropic()


def _build_system_prompt(session: Session) -> str:
    """Erstat placeholder med liste af aktive konkurrenter (navn + slug + aliasser)."""
    template = PROMPT_PATH.read_text(encoding="utf-8")
    competitors = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712
    lines: list[str] = []
    for c in competitors:
        aliases = (c.scraper_config or {}).get("geo", {}).get("aliases") or []
        names = [c.name] + list(aliases)
        # Format: "navn / alias1 / alias2 -> slug"
        names_str = " / ".join(sorted(set(names), key=lambda n: -len(n))[:5])
        lines.append(f"  - {names_str} -> {c.slug}")
    return template.replace("COMPETITOR_LIST_PLACEHOLDER", "\n".join(lines))


def classify_one(client: Anthropic, system_prompt: str, article: IndustryArticle) -> dict[str, Any] | None:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
        system=[
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Source: {article.source}\n"
                    f"Title: {article.title}\n\n"
                    f"Description: {(article.description or '')[:2000]}"
                ),
            }
        ],
    )
    text = response.content[0].text.strip() if response.content else ""
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("industry_classify.invalid_json", article_id=article.id, raw=text[:200])
        return None
    if not all(field in data for field in EXPECTED_FIELDS):
        logger.warning("industry_classify.missing_fields", article_id=article.id, got=list(data.keys()))
        return None
    return data


def classify_pending(session: Session, limit: int = 300) -> dict[str, int]:
    """Klassificer alle IndustryArticle-rows der ikke er klassificerede endnu."""
    client = _client()
    if client is None:
        logger.warning("industry_classify.skipped", reason="no_api_key")
        return {"processed": 0, "updated": 0, "reason": "no_api_key"}

    system_prompt = _build_system_prompt(session)
    pending = list(
        session.exec(
            select(IndustryArticle)
            .where(IndustryArticle.is_classified.is_(False))  # type: ignore[union-attr]
            .limit(limit)
        ).all()
    )

    updated = 0
    now = datetime.utcnow()
    for article in pending:
        data = classify_one(client, system_prompt, article)
        if data is None:
            continue
        article.topic = str(data["topic"])[:50]
        article.geo_scope = str(data["geo_scope"])[:20]
        mentions = data.get("mentioned_competitors") or []
        article.mentioned_competitors = [str(m)[:50] for m in mentions if m][:10]
        article.is_classified = True
        article.classified_at = now
        session.add(article)
        updated += 1
        if updated % 25 == 0:
            session.commit()

    session.commit()
    logger.info("industry_classify.done", processed=len(pending), updated=updated)
    return {"processed": len(pending), "updated": updated}
