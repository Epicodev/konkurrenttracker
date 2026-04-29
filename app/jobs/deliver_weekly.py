"""Cron-entrypoint: byg + send ugens rapport.

Koeres som: python -m app.jobs.deliver_weekly

Bygger payload, render PDF, sender via Postmark, gemmer Report-row.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import structlog
from sqlmodel import Session, select

from app.db import engine
from app.delivery.mailer import send_weekly_report
from app.models import Report
from app.reporting.builder import build_payload

logger = structlog.get_logger(__name__)
PDF_DIR = Path(os.environ.get("PDF_STORAGE_PATH", "/tmp/konkurrent-rapporter"))


def _iso_week(dt: datetime) -> str:
    iso_year, iso_week, _ = dt.isocalendar()
    return f"{iso_year}-W{iso_week:02d}"


def deliver(week: str | None = None) -> dict:
    week = week or _iso_week(datetime.utcnow())
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    with Session(engine) as session:
        payload = build_payload(session, week=week)
    signal_count = len(payload["signals"])
    data_points = payload["stats"]["jobs_last_7d"] + payload["stats"]["events_last_7d"]

    try:
        from app.reporting.pdf import render_pdf

        pdf_bytes = render_pdf("weekly_report.html", payload)
    except Exception as exc:
        logger.exception("deliver.pdf_failed", error=str(exc))
        with Session(engine) as session:
            existing = session.exec(select(Report).where(Report.week == week)).first()
            report = existing or Report(week=week)
            report.signal_count = signal_count
            report.data_points = data_points
            report.status = "failed"
            session.add(report)
            session.commit()
        return {"status": "failed", "reason": f"pdf_render: {exc}"}

    pdf_path = PDF_DIR / f"epico-uge-{week}.pdf"
    pdf_path.write_bytes(pdf_bytes)

    mail_result = send_weekly_report(pdf_bytes, week=week)

    with Session(engine) as session:
        existing = session.exec(select(Report).where(Report.week == week)).first()
        report = existing or Report(week=week)
        report.pdf_path = str(pdf_path)
        report.signal_count = signal_count
        report.data_points = data_points
        report.exec_summary = payload.get("top_tagline")
        if mail_result.get("sent", 0) > 0:
            report.status = "sent"
            report.sent_at = datetime.utcnow()
        elif mail_result.get("skipped"):
            report.status = "pending"
        else:
            report.status = "failed"
        session.add(report)
        session.commit()

    return {
        "week": week,
        "pdf_size_kb": round(len(pdf_bytes) / 1024, 1),
        "pdf_path": str(pdf_path),
        "signal_count": signal_count,
        "data_points": data_points,
        "mail": mail_result,
    }


def main() -> int:
    result = deliver()
    print(f"[deliver] {result}")
    return 0 if result.get("mail", {}).get("failed", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
