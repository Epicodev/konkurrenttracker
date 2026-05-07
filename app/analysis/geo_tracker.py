"""GEO (Generative Engine Optimization) tracker.

Maaler "share of voice" - hvor ofte hver konkurrent bliver naevnt af en AI
naar man stiller realistiske brugersporgsmal om rekruttering / IT-konsulent /
bemanding i Danmark.

Pipeline pr. uge:
1. Stil N standardprompts mod Claude (kunne udvides til OpenAI/Perplexity).
2. Tael for hver konkurrent hvor mange svar deres navn (eller alias) optraeder i.
3. Lad Claude vurdere overordnet sentiment for hvert firma paa tvaers af svarene.
4. Gem GeoMention-row pr. (week, competitor, ai_engine).

Hvis ANTHROPIC_API_KEY ikke er sat: skip aabent.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import structlog
from anthropic import Anthropic
from sqlmodel import Session, select

from app.models import Competitor, GeoMention

logger = structlog.get_logger(__name__)

CLAUDE_MODEL = "claude-sonnet-4-6"
SYSTEM_PROMPT = (Path(__file__).parent / "prompts" / "geo_queries.md").read_text(encoding="utf-8")

# Realistiske brugersporgsmal for Epicos marked. Sigter efter situationer hvor
# Epico OG konkurrenter er relevante svar. Hold den tight - flere prompts =
# flere API-kald, men ogsaa stoerre stikprove.
DEFAULT_PROMPTS: list[str] = [
    "Hvilke konsulenthuse i Danmark er bedst til at finde SAP-konsulenter?",
    "Hvem er de stoerste IT-bemandingsbureauer i Koebenhavn?",
    "Hvilket rekrutteringsbureau bruger jeg hvis jeg skal hyre en senior backend-udvikler i Aarhus?",
    "Hvilke danske firmaer er specialiseret i freelance IT-konsulenter?",
    "Hvem er de bedste headhuntere til tech-roller i Danmark?",
    "Anbefal et konsulenthus til at hjaelpe med digital transformation i Danmark.",
    "Hvilke firmaer udlejer software-udviklere paa kontrakt i Danmark?",
    "Hvem skal jeg kontakte for at finde en interim CTO i Danmark?",
    "Hvilke rekrutteringsfirmaer er specialiseret i data scientists og ML-engineers i Danmark?",
    "Hvis jeg skal opskalere et tech-team hurtigt i Koebenhavn, hvilke bureauer kan hjaelpe?",
]

SENTIMENT_PROMPT = """Du er en upartisk analytiker. Nedenfor er N AI-svar paa
forskellige brugerspoergsmaal hvor firmaet "{name}" blev naevnt.

For hvert svar - hvordan beskrives firmaet? Klassificer den samlede sentiment
paa tvaers af alle svarene som EET af:

- positive: tydelig positiv anbefaling, fremhaeves som ledende eller bedst
- neutral: naevnes som mulighed blandt andre, ingen klar holdning
- negative: kritik eller advarsel
- mixed: tydelig blanding af positive og negative omtaler

Svar udelukkende med een af de fire vaerdier - intet andet.

AI-SVARENE:
{quotes}
"""


def _client() -> Anthropic | None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return None
    return Anthropic()


def _iso_week(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def _aliases(competitor: Competitor) -> list[str]:
    """Returner liste af navne/aliasses vi vil match paa i AI-svar."""
    config: dict[str, Any] = competitor.scraper_config or {}
    aliases: list[str] = []
    name = (competitor.name or "").strip()
    if name:
        aliases.append(name)
        # Trim af A/S, ApS, "Specialist Recruitment", "Denmark", etc. for bredere match
        short = re.sub(r"\s+(A/S|ApS|Specialist Recruitment|Recruitment|Denmark|Danmark)\b.*", "", name, flags=re.IGNORECASE).strip()
        if short and short.lower() != name.lower():
            aliases.append(short)
    # Eksplicitte aliasses fra config
    extra = config.get("geo", {}).get("aliases") or []
    aliases.extend(str(a) for a in extra)
    # Domaen-baseret alias (ex: "epico.dk" -> "epico")
    if competitor.domain:
        host = competitor.domain.replace("https://", "").replace("http://", "").split("/")[0]
        token = host.split(".")[0]
        if token and len(token) >= 3:
            aliases.append(token)
    # Dedup case-insensitive
    seen = set()
    unique: list[str] = []
    for a in aliases:
        key = a.lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(a.strip())
    return unique


def _mentions_in(text: str, aliases: list[str]) -> bool:
    """True hvis et af aliasses optraeder som ord i teksten (case-insensitive)."""
    lowered = text.lower()
    for alias in aliases:
        # Word-boundary match - undgaa fx "Hays" matcher "haystack"
        if re.search(r"\b" + re.escape(alias.lower()) + r"\b", lowered):
            return True
    return False


def _classify_sentiment(client: Anthropic, name: str, quotes: list[str]) -> str:
    if not quotes:
        return "neutral"
    joined = "\n\n---\n\n".join(quotes[:5])  # max 5 svar for at holde prompt-stoerrelse nede
    response = client.messages.create(
        model="claude-haiku-4-5",
        max_tokens=20,
        messages=[
            {
                "role": "user",
                "content": SENTIMENT_PROMPT.format(name=name, quotes=joined),
            }
        ],
    )
    text = (response.content[0].text if response.content else "").strip().lower()
    for candidate in ("positive", "negative", "mixed", "neutral"):
        if candidate in text:
            return candidate
    return "neutral"


def run_geo_pass(session: Session, prompts: list[str] | None = None) -> dict[str, Any]:
    """Koer alle prompts mod Claude, tael mentions pr. konkurrent, gem GeoMention-rows."""
    client = _client()
    if client is None:
        logger.warning("geo.skipped", reason="no_api_key")
        return {"runs": 0, "competitors_tracked": 0, "reason": "no_api_key"}

    prompts = prompts or DEFAULT_PROMPTS
    competitors = list(session.exec(select(Competitor).where(Competitor.active == True)).all())  # noqa: E712
    if not competitors:
        return {"runs": 0, "competitors_tracked": 0, "reason": "no_competitors"}

    # Indsaml svar
    answers: list[str] = []
    for prompt in prompts:
        try:
            response = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=600,
                system=[
                    {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
                ],
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text if response.content else ""
            answers.append(text)
        except Exception as exc:  # noqa: BLE001
            logger.exception("geo.prompt_failed", prompt=prompt[:80], error=str(exc))

    if not answers:
        return {"runs": 0, "competitors_tracked": 0, "reason": "no_answers"}

    week = _iso_week(datetime.utcnow())
    ai_engine = "claude"

    # Slet eksisterende rows for samme (week, ai_engine) saa re-koersel ikke duplikerer
    existing = session.exec(
        select(GeoMention).where(GeoMention.week == week, GeoMention.ai_engine == ai_engine)
    ).all()
    for row in existing:
        session.delete(row)

    tracked = 0
    for competitor in competitors:
        aliases = _aliases(competitor)
        if not aliases:
            continue
        matching: list[str] = [a for a in answers if _mentions_in(a, aliases)]
        mentions = len(matching)
        share = mentions / len(answers) if answers else 0.0
        sentiment = _classify_sentiment(client, competitor.name, matching) if matching else "neutral"
        # Gem 3 korte uddrag af matching-svar som dokumentation
        samples = []
        for ans in matching[:3]:
            snippet = ans.strip()
            if len(snippet) > 400:
                snippet = snippet[:400] + "..."
            samples.append(snippet)
        session.add(
            GeoMention(
                week=week,
                competitor_id=competitor.id,  # type: ignore[arg-type]
                ai_engine=ai_engine,
                mentions=mentions,
                total_queries=len(answers),
                share_of_voice=round(share, 4),
                sentiment=sentiment,
                sample_quotes=samples,
            )
        )
        tracked += 1

    session.commit()
    logger.info("geo.done", week=week, ai_engine=ai_engine, runs=len(answers), competitors=tracked)
    return {
        "week": week,
        "ai_engine": ai_engine,
        "runs": len(answers),
        "competitors_tracked": tracked,
    }
