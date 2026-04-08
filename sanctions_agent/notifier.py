"""
Notification module – sends sync reports via Slack webhook and/or SMTP email.

Configure via env vars (all optional):
  SLACK_WEBHOOK_URL   – Incoming Webhook URL from your Slack app
  NOTIFY_EMAIL_TO     – comma-separated recipient addresses
  SMTP_HOST           – default: smtp.gmail.com
  SMTP_PORT           – default: 587
  SMTP_USER           – sender address
  SMTP_PASSWORD       – app password (not your account password)
"""
import logging
import os
import smtplib
import json
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

SLACK_WEBHOOK_URL: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")
NOTIFY_EMAIL_TO:   Optional[str] = os.getenv("NOTIFY_EMAIL_TO")
SMTP_HOST:  str = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT:  int = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER:  Optional[str] = os.getenv("SMTP_USER")
SMTP_PASS:  Optional[str] = os.getenv("SMTP_PASSWORD")


# ── Main send function ────────────────────────────────────────────────────────

def notify_sync_complete(
    delta_stats: Dict[str, int],
    upsert_stats: Dict[str, int],
    errors: List[str],
    duration_seconds: float,
):
    """
    Send a sync completion notification via all configured channels.
    Silently skips if no channels are configured.
    """
    report = _build_report(delta_stats, upsert_stats, errors, duration_seconds)

    if SLACK_WEBHOOK_URL:
        _send_slack(report)
    if NOTIFY_EMAIL_TO and SMTP_USER and SMTP_PASS:
        _send_email(report, errors)


def notify_sync_failed(source: str, error: str):
    """Called when an entire source fetch fails."""
    msg = f"⚠️ Sanctions Agent – `{source}` fetch FAILED: {error}"
    if SLACK_WEBHOOK_URL:
        _post_slack_text(msg)


# ── Report builder ────────────────────────────────────────────────────────────

def _build_report(
    delta: Dict[str, int],
    upsert: Dict[str, int],
    errors: List[str],
    duration: float,
) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = "✅ SUCCESS" if not errors else "⚠️  PARTIAL"
    return {
        "timestamp": ts,
        "status": status,
        "duration_s": round(duration, 1),
        "delta": delta,
        "upsert": upsert,
        "errors": errors,
    }


# ── Slack ─────────────────────────────────────────────────────────────────────

def _send_slack(report: dict):
    d = report["delta"]
    u = report["upsert"]
    err_text = "\n".join(f"• {e}" for e in report["errors"]) if report["errors"] else "None"

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Sanctions Sync Report – {report['timestamp']}",
            },
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status*\n{report['status']}"},
                {"type": "mrkdwn", "text": f"*Duration*\n{report['duration_s']}s"},
                {"type": "mrkdwn", "text": f"*New entities*\n{d.get('new', 0)}"},
                {"type": "mrkdwn", "text": f"*Modified*\n{d.get('modified', 0)}"},
                {"type": "mrkdwn", "text": f"*Removed*\n{d.get('removed', 0)}"},
                {"type": "mrkdwn", "text": f"*Unchanged*\n{d.get('unchanged', 0)}"},
                {"type": "mrkdwn", "text": f"*DB errors*\n{u.get('errors', 0)}"},
            ],
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"*Errors*\n{err_text}"},
        },
    ]

    try:
        resp = httpx.post(
            SLACK_WEBHOOK_URL,
            json={"blocks": blocks},
            timeout=10,
        )
        resp.raise_for_status()
        logger.info("Slack notification sent")
    except Exception as exc:
        logger.error("Slack notification failed: %s", exc)


def _post_slack_text(text: str):
    if not SLACK_WEBHOOK_URL:
        return
    try:
        httpx.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    except Exception as exc:
        logger.error("Slack post failed: %s", exc)


# ── Email ─────────────────────────────────────────────────────────────────────

def _send_email(report: dict, errors: List[str]):
    recipients = [r.strip() for r in NOTIFY_EMAIL_TO.split(",") if r.strip()]
    if not recipients:
        return

    d = report["delta"]
    u = report["upsert"]
    subject = f"[Sanctions Agent] {report['status']} – {report['timestamp']}"

    html = f"""
    <html><body style="font-family:sans-serif;max-width:600px">
    <h2>Sanctions Sync Report</h2>
    <p><strong>Time:</strong> {report['timestamp']}<br>
       <strong>Status:</strong> {report['status']}<br>
       <strong>Duration:</strong> {report['duration_s']}s</p>

    <table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
      <tr><th>Metric</th><th>Count</th></tr>
      <tr><td>New entities</td><td>{d.get('new', 0)}</td></tr>
      <tr><td>Modified</td><td>{d.get('modified', 0)}</td></tr>
      <tr><td>Removed (soft-deleted)</td><td>{d.get('removed', 0)}</td></tr>
      <tr><td>Unchanged (skipped)</td><td>{d.get('unchanged', 0)}</td></tr>
      <tr><td>DB errors</td><td>{u.get('errors', 0)}</td></tr>
    </table>

    {"<h3>⚠️ Errors</h3><ul>" + "".join(f"<li>{e}</li>" for e in errors) + "</ul>" if errors else ""}
    </body></html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = SMTP_USER
    msg["To"]      = ", ".join(recipients)
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
            srv.ehlo()
            srv.starttls()
            srv.login(SMTP_USER, SMTP_PASS)
            srv.sendmail(SMTP_USER, recipients, msg.as_string())
        logger.info("Email notification sent to %s", recipients)
    except Exception as exc:
        logger.error("Email notification failed: %s", exc)
