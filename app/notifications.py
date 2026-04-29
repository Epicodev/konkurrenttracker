"""Simpel Slack-webhook-poster.

Bruges til driftsalerts. Hvis SLACK_WEBHOOK_URL ikke er sat (typisk lokalt), no-op.
"""

import os

import httpx
import structlog

logger = structlog.get_logger(__name__)


def slack_alert(text: str) -> None:
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        logger.info("slack.skipped", reason="no_webhook_configured", text=text[:200])
        return
    try:
        response = httpx.post(webhook_url, json={"text": text}, timeout=10.0)
        response.raise_for_status()
        logger.info("slack.sent", text=text[:200])
    except Exception as exc:  # noqa: BLE001
        logger.exception("slack.failed", text=text[:200], error=str(exc))
