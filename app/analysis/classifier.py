"""Haiku-baseret klassificering af jobopslag.

Henter opslag uden category/seniority og kalder Claude Haiku 4.5 for at udlede
felter. System-prompt cachees via prompt-caching for at saenke per-call cost.

Hvis ANTHROPIC_API_KEY ikke er sat: skipper aabnt med en advarsel (lokal dev).
"""

import json
import os
from pathlib import Path

import structlog
from anthropic import Anthropic
from sqlmodel import Session, select

from app.models import JobPosting

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-haiku-4-5"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "classify_job.md").read_text(encoding="utf-8")

# Felter vi forventer at modtage tilbage
EXPECTED_FIELDS = ("category", "seniority", "is_freelance", "confidence")


def _client() -> Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Anthropic()


def classify_one(client: Anthropic, posting: JobPosting) -> dict[str, str | bool] | None:
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=300,
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
                    f"Title: {posting.title}\n\n"
                    f"Description: {(posting.description or '')[:3000]}"
                ),
            }
        ],
    )
    text = response.content[0].text.strip() if response.content else ""
    # Strip markdown code fences hvis modellen alligevel laver dem
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("classify.invalid_json", posting_id=posting.id, raw=text[:200])
        return None
    if not all(field in data for field in EXPECTED_FIELDS):
        logger.warning("classify.missing_fields", posting_id=posting.id, got=list(data.keys()))
        return None
    return data


def classify_pending(session: Session, limit: int = 100) -> dict[str, int]:
    """Klassificer alle JobPosting-rows uden category. Returner {processed, updated, skipped}."""
    client = _client()
    if client is None:
        logger.warning("classify.skipped", reason="no_api_key")
        return {"processed": 0, "updated": 0, "skipped": 0, "reason": "no_api_key"}

    pending = list(
        session.exec(
            select(JobPosting).where(JobPosting.category.is_(None)).limit(limit)  # type: ignore[union-attr]
        ).all()
    )

    updated = 0
    for posting in pending:
        data = classify_one(client, posting)
        if data is None:
            continue
        posting.category = str(data["category"])[:100]
        posting.seniority = str(data["seniority"])[:50]
        posting.is_freelance = bool(data["is_freelance"])
        session.add(posting)
        updated += 1

    session.commit()
    logger.info("classify.done", processed=len(pending), updated=updated)
    return {"processed": len(pending), "updated": updated}
