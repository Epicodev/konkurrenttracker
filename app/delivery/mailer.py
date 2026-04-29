"""Postmark-integration: send ugentlig PDF-rapport til distributionslisten.

Postmark API: https://postmarkapp.com/developer/api/email-api
Vi bruger /email-endpointet med en attachment-array.
"""

import base64
import os
from pathlib import Path
from typing import Any

import httpx
import structlog
import yaml

logger = structlog.get_logger(__name__)

POSTMARK_API_URL = "https://api.postmarkapp.com/email"
DISTRIBUTION_PATH = Path(__file__).parent.parent.parent / "config" / "distribution.yaml"


def _load_recipients() -> list[dict[str, str]]:
    if not DISTRIBUTION_PATH.exists():
        logger.warning("mailer.no_distribution_file", path=str(DISTRIBUTION_PATH))
        return []
    with DISTRIBUTION_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("recipients", [])


def send_weekly_report(
    pdf_bytes: bytes,
    week: str,
    subject: str | None = None,
    body_html: str | None = None,
) -> dict[str, Any]:
    """Send PDF som attachment til alle modtagere i distribution.yaml.

    Returner dict med 'sent', 'failed', 'recipients_count'. Hvis POSTMARK_SERVER_TOKEN
    eller from-adresse mangler, returner skipped uden at sende.
    """
    token = os.environ.get("POSTMARK_SERVER_TOKEN")
    from_email = os.environ.get("POSTMARK_FROM_EMAIL")
    if not token or not from_email:
        logger.warning("mailer.skipped", reason="missing_postmark_config")
        return {"sent": 0, "failed": 0, "skipped": True, "reason": "missing_postmark_config"}

    recipients = _load_recipients()
    if not recipients:
        return {"sent": 0, "failed": 0, "skipped": True, "reason": "no_recipients"}

    subject = subject or f"Epico konkurrent-rapport · uge {week}"
    body_html = body_html or (
        f"<p>Den ugentlige rapport for uge <strong>{week}</strong> er vedhaeftet.</p>"
        f"<p>Tjek dashboardet for filtrering og dybere udforskning.</p>"
    )
    pdf_b64 = base64.b64encode(pdf_bytes).decode("ascii")
    attachment = {
        "Name": f"epico-uge-{week}.pdf",
        "Content": pdf_b64,
        "ContentType": "application/pdf",
    }

    sent = 0
    failed: list[str] = []
    headers = {"X-Postmark-Server-Token": token, "Accept": "application/json"}

    with httpx.Client(timeout=30.0) as client:
        for recipient in recipients:
            payload = {
                "From": from_email,
                "To": recipient["email"],
                "Subject": subject,
                "HtmlBody": body_html,
                "Attachments": [attachment],
                "MessageStream": "outbound",
            }
            try:
                response = client.post(POSTMARK_API_URL, json=payload, headers=headers)
                response.raise_for_status()
                sent += 1
                logger.info("mailer.sent", recipient=recipient["email"], week=week)
            except Exception as exc:  # noqa: BLE001
                logger.exception("mailer.failed", recipient=recipient["email"], error=str(exc))
                failed.append(f"{recipient['email']}: {exc}")

    return {"sent": sent, "failed": len(failed), "recipients_count": len(recipients), "errors": failed}
