"""Cron-entrypoint: koerer ugentlig analyse - klassificer + syntetiser.

Koeres som: python -m app.jobs.analyze_weekly
"""

import sys

from sqlmodel import Session

from app.analysis.classifier import classify_pending
from app.analysis.synthesizer import synthesize_week
from app.db import engine


def main() -> int:
    with Session(engine) as session:
        classify_result = classify_pending(session)
        print(f"[classify] {classify_result}")

    with Session(engine) as session:
        synth_result = synthesize_week(session)
        print(f"[synthesize] {synth_result}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
