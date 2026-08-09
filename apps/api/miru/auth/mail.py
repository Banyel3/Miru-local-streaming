"""The one email Miru ever sends: here is your sign-in link, or type this code.

Resend needs a verified sending domain before it will deliver to anyone but
the account owner — see docs/plans/go-live-auth.md. Faked at this seam in
every test; a test that emails a real person is a bug.
"""

from __future__ import annotations

import logging

import httpx

from miru.core.config import settings

log = logging.getLogger(__name__)


def send_login_email(email: str, link: str, code: str) -> None:
    """Raises on failure — the caller decides that failure must stay silent."""
    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {settings.resend_api_key}"},
        json={
            "from": settings.mail_from,
            "to": [email],
            "subject": f"Your Miru sign-in code: {code}",
            "html": (
                f'<p><a href="{link}">Click to sign in to Miru</a> — the link works '
                f"once and expires in 15 minutes.</p>"
                f"<p>On another device? Enter this code instead: <strong>{code}</strong></p>"
                f"<p>If you didn't ask for this, ignore it.</p>"
            ),
        },
        timeout=10.0,
    )
    resp.raise_for_status()
