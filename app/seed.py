"""Idempotent seed script - opretter de 10 konkurrenter hvis de ikke allerede findes.

Koeres som: python -m app.seed
"""

from sqlmodel import Session, select

from app.db import engine
from app.models import Competitor

COMPETITORS: list[dict[str, str | None]] = [
    {"slug": "prodata", "name": "ProData Consult A/S", "cvr": None, "domain": "prodata.dk"},
    {"slug": "right-people", "name": "Right People Group", "cvr": None, "domain": "rightpeoplegroup.com"},
    {"slug": "hays", "name": "Hays Denmark", "cvr": None, "domain": "hays.dk"},
    {"slug": "zen", "name": "Zen Consulting", "cvr": None, "domain": "zen.dk"},
    {"slug": "brainville", "name": "Brainville", "cvr": None, "domain": "brainville.com"},
    {"slug": "competitor-06", "name": "Konkurrent 06 (placeholder)", "cvr": None, "domain": None},
    {"slug": "competitor-07", "name": "Konkurrent 07 (placeholder)", "cvr": None, "domain": None},
    {"slug": "competitor-08", "name": "Konkurrent 08 (placeholder)", "cvr": None, "domain": None},
    {"slug": "competitor-09", "name": "Konkurrent 09 (placeholder)", "cvr": None, "domain": None},
    {"slug": "competitor-10", "name": "Konkurrent 10 (placeholder)", "cvr": None, "domain": None},
]


def seed() -> None:
    with Session(engine) as session:
        existing_slugs = set(session.exec(select(Competitor.slug)).all())
        added = 0
        for entry in COMPETITORS:
            if entry["slug"] in existing_slugs:
                continue
            session.add(Competitor(**entry))
            added += 1
        session.commit()
        total = len(session.exec(select(Competitor)).all())
        print(f"Seed faerdig: {added} ny(e), {total} i alt.")


if __name__ == "__main__":
    seed()
