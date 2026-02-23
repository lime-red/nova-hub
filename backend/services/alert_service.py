"""
Out-of-band alert delivery service for Nova Hub.

Delivers sequence-gap alerts via:
  - Email  (SMTP / STARTTLS)
  - Webhook (HTTP POST, optional HMAC-SHA256 signature)

Call `dispatch_alert(alert, league_name)` from an async context to queue
delivery.  Failures are logged but never propagate — alert delivery must not
crash packet processing.
"""

import asyncio
import hashlib
import hmac
import json
import smtplib
import ssl
from datetime import datetime, timezone
from email.mime.text import MIMEText
from typing import TYPE_CHECKING

from backend.logging_config import get_logger

if TYPE_CHECKING:
    from backend.models.database import SequenceAlert

logger = get_logger(context="alert_service")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def dispatch_alert(alert: "SequenceAlert", league_name: str) -> None:
    """
    Dispatch a new sequence-gap alert to all configured channels.

    This function is fire-and-forget: it launches background tasks for each
    channel and returns immediately.  Failures are logged, not raised.
    """
    from backend.core.config import get_config
    cfg = get_config().alerting

    if not cfg.enabled:
        return

    if cfg.email.enabled and cfg.email.to_addresses:
        asyncio.create_task(_deliver_email(alert, league_name, cfg.email))

    if cfg.webhook.enabled and cfg.webhook.url:
        asyncio.create_task(_deliver_webhook(alert, league_name, cfg.webhook))


# ---------------------------------------------------------------------------
# Email delivery
# ---------------------------------------------------------------------------

async def _deliver_email(alert, league_name: str, cfg) -> None:
    """Send alert email via SMTP (STARTTLS).  Retries are not attempted."""
    try:
        await asyncio.to_thread(_send_email_sync, alert, league_name, cfg)
        logger.info(
            f"Alert email sent for sequence gap on {league_name} "
            f"route {alert.source_bbs_index}->{alert.dest_bbs_index}"
        )
    except Exception as e:
        logger.error(f"Failed to send alert email: {e}")


def _send_email_sync(alert, league_name: str, cfg) -> None:
    detected_at = _fmt_dt(alert.detected_at)
    subject = (
        f"[Nova Hub] Sequence gap detected: {league_name} "
        f"{alert.source_bbs_index}->{alert.dest_bbs_index}"
    )
    body = (
        f"A packet sequence gap has been detected on Nova Hub.\n\n"
        f"  League  : {league_name}\n"
        f"  Route   : {alert.source_bbs_index} -> {alert.dest_bbs_index}\n"
        f"  Missing : {alert.expected_sequence:03d}\n"
        f"  Received: {alert.received_sequence:03d}\n"
        f"  Gap size: {alert.gap_size} packet(s)\n"
        f"  Detected: {detected_at}\n\n"
        f"Please check whether the missing packets were lost in transit.\n"
        f"Resolve this alert in the Nova Hub dashboard once investigated.\n"
    )

    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = cfg.from_address
    msg["To"] = ", ".join(cfg.to_addresses)

    if cfg.use_tls:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout) as smtp:
            smtp.starttls(context=ssl.create_default_context())
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.sendmail(cfg.from_address, cfg.to_addresses, msg.as_string())
    else:
        with smtplib.SMTP(cfg.smtp_host, cfg.smtp_port, timeout=cfg.timeout) as smtp:
            if cfg.smtp_user:
                smtp.login(cfg.smtp_user, cfg.smtp_password)
            smtp.sendmail(cfg.from_address, cfg.to_addresses, msg.as_string())


# ---------------------------------------------------------------------------
# Webhook delivery
# ---------------------------------------------------------------------------

async def _deliver_webhook(alert, league_name: str, cfg) -> None:
    """POST alert JSON to the configured webhook URL with optional HMAC signature."""
    payload = {
        "event": "sequence_gap",
        "alert": {
            "league": league_name,
            "route": f"{alert.source_bbs_index} -> {alert.dest_bbs_index}",
            "expected_sequence": alert.expected_sequence,
            "received_sequence": alert.received_sequence,
            "gap_size": alert.gap_size,
            "detected_at": _fmt_dt(alert.detected_at),
        },
    }
    body = json.dumps(payload).encode()

    headers = {"Content-Type": "application/json"}
    if cfg.secret:
        sig = hmac.new(cfg.secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-Nova-Hub-Signature"] = f"sha256={sig}"

    for attempt in range(1, cfg.retry_attempts + 1):
        try:
            await _post_webhook(cfg.url, body, headers, cfg.timeout_seconds)
            logger.info(
                f"Alert webhook delivered for {league_name} "
                f"{alert.source_bbs_index}->{alert.dest_bbs_index}"
            )
            return
        except Exception as e:
            if attempt < cfg.retry_attempts:
                wait = 2 ** (attempt - 1)  # 1s, 2s, …
                logger.warning(
                    f"Webhook delivery attempt {attempt} failed ({e}); "
                    f"retrying in {wait}s"
                )
                await asyncio.sleep(wait)
            else:
                logger.error(
                    f"Webhook delivery failed after {cfg.retry_attempts} attempt(s): {e}"
                )


async def _post_webhook(url: str, body: bytes, headers: dict, timeout: int) -> None:
    import aiohttp
    async with aiohttp.ClientSession() as session:
        async with session.post(
            url,
            data=body,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=timeout),
        ) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"Webhook returned HTTP {resp.status}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "unknown"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
