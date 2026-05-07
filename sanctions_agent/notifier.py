"""
Notification module – sends sync reports via Brevo (REST API) and/or Slack.

Configure via env vars (all optional):
  BREVO_API_KEY      – API key from Brevo dashboard (xkeysib-...)
  BREVO_FROM_EMAIL   – verified sender address
  BREVO_FROM_NAME    – sender display name (default: "Sanctions Agent")
  BREVO_TO_EMAIL     – comma-separated recipient addresses
  SLACK_WEBHOOK_URL  – optional, posts a short status message
"""
import logging
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


# ── Config ────────────────────────────────────────────────────────────────────

BREVO_API_KEY:    Optional[str] = os.getenv("BREVO_API_KEY")
BREVO_FROM_EMAIL: Optional[str] = os.getenv("BREVO_FROM_EMAIL")
BREVO_FROM_NAME:  str           = os.getenv("BREVO_FROM_NAME", "Sanctions Agent")
BREVO_TO_EMAIL:   Optional[str] = os.getenv("BREVO_TO_EMAIL")

SLACK_WEBHOOK_URL: Optional[str] = os.getenv("SLACK_WEBHOOK_URL")

BREVO_ENDPOINT = "https://api.brevo.com/v3/smtp/email"


# ── Public API ────────────────────────────────────────────────────────────────

def notify_sync_complete(
    delta_stats: Dict[str, int],
    upsert_stats: Dict[str, int],
    errors: List[str],
    duration_seconds: float,
):
    """Send a sync completion notification (success or partial)."""
    report = _build_report(delta_stats, upsert_stats, errors, duration_seconds)
    subject = f"[Sanctions Agent] {report['status']} – {report['timestamp']}"
    html = _render_html(report)

    _send_brevo(subject, html)
    if SLACK_WEBHOOK_URL:
        _send_slack(report)


def notify_sync_failed(source: str, error: str):
    """Called when a single source fetch fails – sends an alert."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    subject = f"[Sanctions Agent] FETCH FAILED – {source} – {ts}"
    html = f"""
    <html><body style="font-family:sans-serif;max-width:600px">
      <h2 style="color:#c00">Sanctions Sync – fetch failure</h2>
      <p><strong>Source:</strong> {source}<br>
         <strong>Time:</strong> {ts}</p>
      <pre style="background:#f4f4f4;padding:12px;white-space:pre-wrap">{error}</pre>
    </body></html>
    """
    _send_brevo(subject, html)
    if SLACK_WEBHOOK_URL:
        _post_slack_text(f"⚠️ Sanctions Agent – `{source}` fetch FAILED: {error}")


# ── Report ────────────────────────────────────────────────────────────────────

def _build_report(
    delta: Dict[str, int],
    upsert: Dict[str, int],
    errors: List[str],
    duration: float,
) -> dict:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    status = "✅ SUCCESS" if not errors else "⚠️ PARTIAL"
    return {
        "timestamp": ts,
        "status": status,
        "duration_s": round(duration, 1),
        "delta": delta,
        "upsert": upsert,
        "errors": errors,
    }


def _render_html(report: dict) -> str:
    d = report["delta"]
    u = report["upsert"]
    errors_block = ""
    if report["errors"]:
        items = "".join(f"<li>{e}</li>" for e in report["errors"])
        errors_block = f"<h3 style='color:#c00'>Errors</h3><ul>{items}</ul>"

    return f"""
    <html><body style="font-family:sans-serif;max-width:640px">
      <h2>Sanctions Sync Report</h2>
      <p>
        <strong>Time:</strong> {report['timestamp']}<br>
        <strong>Status:</strong> {report['status']}<br>
        <strong>Duration:</strong> {report['duration_s']}s
      </p>

      <table border="1" cellpadding="6" cellspacing="0"
             style="border-collapse:collapse;font-size:14px">
        <tr style="background:#f4f4f4"><th>Metric</th><th>Count</th></tr>
        <tr><td>New entities</td><td>{d.get('new', 0)}</td></tr>
        <tr><td>Modified</td><td>{d.get('modified', 0)}</td></tr>
        <tr><td>Unchanged</td><td>{d.get('unchanged', 0)}</td></tr>
        <tr><td>Removed (gone from source)</td><td>{d.get('removed', 0)}</td></tr>
        <tr><td>Inserted into DB</td><td>{u.get('inserted', 0)}</td></tr>
        <tr><td>Sources wiped &amp; reloaded</td><td>{u.get('sources_wiped', 0)}</td></tr>
        <tr><td>DB errors</td><td>{u.get('errors', 0)}</td></tr>
      </table>

      {errors_block}
    </body></html>
    """


# ── Brevo ─────────────────────────────────────────────────────────────────────

def _send_brevo(subject: str, html: str):
    if not (BREVO_API_KEY and BREVO_FROM_EMAIL and BREVO_TO_EMAIL):
        logger.info("Brevo not configured – skipping email")
        return

    recipients = [
        {"email": addr.strip()}
        for addr in BREVO_TO_EMAIL.split(",")
        if addr.strip()
    ]
    if not recipients:
        return

    payload = {
        "sender": {"email": BREVO_FROM_EMAIL, "name": BREVO_FROM_NAME},
        "to": recipients,
        "subject": subject,
        "htmlContent": html,
    }
    headers = {
        "api-key": BREVO_API_KEY,
        "accept": "application/json",
        "content-type": "application/json",
    }

    try:
        resp = httpx.post(BREVO_ENDPOINT, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        logger.info("Brevo email sent to %s", [r["email"] for r in recipients])
    except httpx.HTTPStatusError as exc:
        logger.error(
            "Brevo email failed – HTTP %s: %s",
            exc.response.status_code, exc.response.text,
        )
    except Exception as exc:
        logger.error("Brevo email failed: %s", exc)


# ── Slack (optional) ──────────────────────────────────────────────────────────

def _send_slack(report: dict):
    d = report["delta"]
    u = report["upsert"]
    err_text = "\n".join(f"• {e}" for e in report["errors"]) if report["errors"] else "None"

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Sanctions Sync – {report['timestamp']}"},
        },
        {
            "type": "section",
            "fields": [
                {"type": "mrkdwn", "text": f"*Status*\n{report['status']}"},
                {"type": "mrkdwn", "text": f"*Duration*\n{report['duration_s']}s"},
                {"type": "mrkdwn", "text": f"*New*\n{d.get('new', 0)}"},
                {"type": "mrkdwn", "text": f"*Modified*\n{d.get('modified', 0)}"},
                {"type": "mrkdwn", "text": f"*Removed*\n{d.get('removed', 0)}"},
                {"type": "mrkdwn", "text": f"*Inserted*\n{u.get('inserted', 0)}"},
                {"type": "mrkdwn", "text": f"*DB errors*\n{u.get('errors', 0)}"},
            ],
        },
        {"type": "section", "text": {"type": "mrkdwn", "text": f"*Errors*\n{err_text}"}},
    ]
    try:
        httpx.post(SLACK_WEBHOOK_URL, json={"blocks": blocks}, timeout=10).raise_for_status()
    except Exception as exc:
        logger.error("Slack notification failed: %s", exc)


def _post_slack_text(text: str):
    try:
        httpx.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10)
    except Exception as exc:
        logger.error("Slack post failed: %s", exc)
