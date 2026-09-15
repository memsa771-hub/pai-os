# -*- coding: utf-8 -*-
"""Transactional email via Resend (https://resend.com/docs/api-reference).

One tiny wrapper instead of an SMTP stack: a single HTTPS POST, keyed by
RESEND_API_KEY. When no key is configured every send becomes a logged no-op.
"""

import logging

import httpx

from app.config import config

logger = logging.getLogger(__name__)

RESEND_URL = "https://api.resend.com/emails"


def email_configured() -> bool:
    return bool(config.RESEND_API_KEY)


def send_email(to: str, subject: str, html_body: str) -> bool:
    """Send one email; returns True on acceptance by the provider.

    Never raises — callers must succeed even when the provider is down or
    unconfigured.
    """
    if not email_configured():
        logger.info("email: RESEND_API_KEY not set, skipping send to %s (%s)", to, subject)
        return False
    try:
        resp = httpx.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {config.RESEND_API_KEY}"},
            json={"from": config.EMAIL_FROM, "to": [to], "subject": subject, "html": html_body},
            timeout=10.0,
        )
        if resp.status_code in (200, 201):
            return True
        logger.warning("email: send to %s failed: %s %s", to, resp.status_code, resp.text[:300])
        return False
    except Exception as e:  # noqa: BLE001 — network errors must not break callers
        logger.warning("email: send to %s failed: %s", to, e)
        return False


