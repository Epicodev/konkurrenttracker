"""Sonnet-baseret ugentlig syntese.

Tager ugens jobopslag + firma-events, sender til Claude Sonnet 4.6 og faar
4-6 prioriterede signaler tilbage. Gemmer som Signal-rows.
"""

import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from anthropic import Anthropic
from sqlmodel import Session, select

from app.models import CompanyEvent, Competitor, JobPosting, Signal

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "weekly_synthesis.md").read_text(encoding="utf-8")


def _client() -> Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Anthropic()


def _iso_week(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _gather_week_data(session: Session, since: datetime) -> dict[str, Any]:
    competitors = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712
    slug_to_id = {c.slug: c.id for c in competitors}

    payload: dict[str, Any] = {"week": _iso_week(since), "competitors": []}
    for c in competitors:
        jobs = list(
            session.exec(
                select(JobPosting).where(
                    JobPosting.competitor_id == c.id,
                    JobPosting.first_seen_at >= since,
                )
            ).all()
        )
        events = list(
            session.exec(
                select(CompanyEvent).where(
                    CompanyEvent.competitor_id == c.id,
                    CompanyEvent.detected_at >= since,
                )
            ).all()
        )
        if not jobs and not events:
            continue
        payload["competitors"].append(
            {
                "slug": c.slug,
                "name": c.name,
                "jobs": [
                    {
                        "id": j.id,
                        "title": j.title,
                        "category": j.category,
                        "seniority": j.seniority,
                        "is_freelance": j.is_freelance,
                        "location": j.location,
                        "source": j.source,
                    }
                    for j in jobs
                ],
                "events": [
                    {
                        "id": e.id,
                        "type": e.event_type,
                        "source": e.source,
                        "title": e.title,
                        "summary": (e.description or "")[:300],
                    }
                    for e in events
                ],
            }
        )
    return payload, slug_to_id


def synthesize_week(session: Session, days_back: int = 7) -> dict[str, Any]:
    """Generer ugens signaler. Erstatter eksisterende signaler for samme uge."""
    client = _client()
    if client is None:
        logger.warning("synthesize.skipped", reason="no_api_key")
        return {"signals_added": 0, "reason": "no_api_key"}

    since = datetime.utcnow() - timedelta(days=days_back)
    week = _iso_week(datetime.utcnow())
    payload, slug_to_id = _gather_week_data(session, since)

    if not payload["competitors"]:
        logger.info("synthesize.no_data", week=week)
        return {"signals_added": 0, "reason": "no_data_in_period"}

    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=4000,
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
                "content": f"Ugens raa data:\n\n```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```",
            }
        ],
    )

    text = response.content[0].text.strip() if response.content else ""
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()
    try:
        signals_data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.exception("synthesize.invalid_json", error=str(exc), raw=text[:500])
        return {"signals_added": 0, "reason": "invalid_json"}

    # Slet eksisterende signaler for samme uge for at undgaa duplikater ved gen-koersel
    for old in session.exec(select(Signal).where(Signal.week == week)).all():
        session.delete(old)

    added = 0
    for entry in signals_data:
        slug = entry.get("competitor_slug")
        competitor_id = slug_to_id.get(slug)
        if competitor_id is None:
            logger.warning("synthesize.unknown_competitor", slug=slug)
            continue
        session.add(
            Signal(
                week=week,
                competitor_id=competitor_id,
                domain=entry.get("domain", "jobs")[:50],
                severity=entry.get("severity", "signal")[:20],
                title=str(entry.get("title", ""))[:500],
                summary=str(entry.get("summary", "")),
                recommended_action=entry.get("recommended_action"),
                recommended_owner=str(entry.get("recommended_owner", ""))[:100] or None,
                confidence=entry.get("confidence", "medium")[:20],
                source_refs=entry.get("source_refs", {}),
            )
        )
        added += 1

    session.commit()
    logger.info("synthesize.done", week=week, added=added)
    return {"signals_added": added, "week": week}
