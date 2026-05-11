"""Haiku-baseret klassificering af market-bred IT-jobopslag.

Beriger MarketJobPosting med category, seniority, tech_stack[], specialization,
is_freelance, is_emerging. System-prompten caches via prompt-caching for at
sænke per-call cost (3000+ kald/uge).

Skip åbent hvis ANTHROPIC_API_KEY ikke er sat.
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

from app.models import MarketJobPosting

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-haiku-4-5"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "classify_market_job.md").read_text(encoding="utf-8")

EXPECTED_FIELDS = ("category", "seniority", "is_freelance", "tech_stack", "specialization", "is_emerging")


def _client() -> Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Anthropic()


def classify_one(client: Anthropic, posting: MarketJobPosting) -> dict[str, Any] | None:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=400,
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
        messages=[
            {
                "role": "user",
                "content": (
                    f"Title: {posting.title}\n"
                    f"Company: {posting.company or '?'}\n"
                    f"Location: {posting.location or '?'}\n\n"
                    f"Description: {(posting.description or '')[:3000]}"
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
        logger.warning("market_classify.invalid_json", posting_id=posting.id, raw=text[:200])
        return None
    if not all(field in data for field in EXPECTED_FIELDS):
        logger.warning("market_classify.missing_fields", posting_id=posting.id, got=list(data.keys()))
        return None
    return data


def classify_pending(session: Session, limit: int = 500) -> dict[str, int]:
    """Klassificer alle MarketJobPosting-rows uden category. Returner counters."""
    client = _client()
    if client is None:
        logger.warning("market_classify.skipped", reason="no_api_key")
        return {"processed": 0, "updated": 0, "reason": "no_api_key"}

    pending = list(
        session.exec(
            select(MarketJobPosting)
            .where(MarketJobPosting.category.is_(None))  # type: ignore[union-attr]
            .limit(limit)
        ).all()
    )

    updated = 0
    now = datetime.utcnow()
    for posting in pending:
        data = classify_one(client, posting)
        if data is None:
            continue
        posting.category = str(data["category"])[:100]
        posting.seniority = str(data["seniority"])[:50]
        posting.is_freelance = bool(data["is_freelance"])
        tech = data.get("tech_stack") or []
        # Begræns til strings, max 8 elementer, max 50 tegn pr. element
        posting.tech_stack = [str(t)[:50] for t in tech if t][:8]
        posting.specialization = str(data["specialization"])[:100]
        posting.is_emerging = bool(data["is_emerging"])
        posting.classified_at = now
        session.add(posting)
        updated += 1
        # Commit hver 25 så lange runs ikke taber alt ved nedbrud
        if updated % 25 == 0:
            session.commit()

    session.commit()
    logger.info("market_classify.done", processed=len(pending), updated=updated)
    return {"processed": len(pending), "updated": updated}
