"""Idempotent seed-script - opretter / opdaterer de 10 konkurrenter.

Koeres som: python -m app.seed

Genkoersel: opdaterer name, active og scraper_config paa eksisterende slugs.
"""

from typing import Any

from sqlmodel import Session, select

from app.db import engine
from app.models import Competitor

COMPETITORS: list[dict[str, Any]] = [
    {
        "slug": "prodata",
        "name": "ProData Consult A/S (nu emagine Consulting A/S)",
        "cvr": "26249627",
        "active": True,
        "scraper_config": {"jobindex": {"query": "ProData"}},
    },
    {
        "slug": "right-people",
        "name": "Right People Group ApS",
        "cvr": "30590627",
        "active": True,
        "scraper_config": {"jobindex": {"query": "Right People"}},
    },
    {
        "slug": "hays",
        "name": "Hays Specialist Recruitment Denmark A/S",
        "cvr": "30908848",
        "active": True,
        "scraper_config": {"jobindex": {"query": "Hays"}},
    },
    {
        "slug": "zen",
        "name": "Zen Consulting",
        "active": True,
        "scraper_config": {"jobindex": {"query": "Zen Consulting"}},
    },
    {
        "slug": "brainville",
        "name": "Brainville",
        "active": True,
        "scraper_config": {"jobindex": {"query": "Brainville"}},
    },
    # Placeholder-konkurrenter: aktive (taeller med i /healthz) men uden scraper-config,
    # saa scraperen springer dem over indtil rigtige firma-data laegges ind.
    {"slug": "competitor-06", "name": "Konkurrent 06 (placeholder)", "active": True, "scraper_config": {}},
    {"slug": "competitor-07", "name": "Konkurrent 07 (placeholder)", "active": True, "scraper_config": {}},
    {"slug": "competitor-08", "name": "Konkurrent 08 (placeholder)", "active": True, "scraper_config": {}},
    {"slug": "competitor-09", "name": "Konkurrent 09 (placeholder)", "active": True, "scraper_config": {}},
    {"slug": "competitor-10", "name": "Konkurrent 10 (placeholder)", "active": True, "scraper_config": {}},
]


def seed() -> None:
    with Session(engine) as session:
        existing = {c.slug: c for c in session.exec(select(Competitor)).all()}
        added = 0
        updated = 0
        for entry in COMPETITORS:
            current = existing.get(entry["slug"])
            if current is None:
                session.add(Competitor(**entry))
                added += 1
            else:
                changed = False
                for key, value in entry.items():
                    if getattr(current, key) != value:
                        setattr(current, key, value)
                        changed = True
                if changed:
                    session.add(current)
                    updated += 1
        session.commit()
        total = len(session.exec(select(Competitor)).all())
        print(f"Seed faerdig: {added} ny(e), {updated} opdateret, {total} i alt.")


if __name__ == "__main__":
    seed()
