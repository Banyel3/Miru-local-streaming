"""Magic-link + OTP auth. nginx asks `check()` about every public request.

The one non-obvious rule, everywhere here: an outsider must not be able to
learn anything from the outside. Unknown email, rate-limited email, dead
Resend — every path does nothing observable and the router answers the same
200. The only oracle is the inbox.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from miru.auth import mail
from miru.auth.models import AuthLogin, AuthRate, AuthSession
from miru.core.config import settings

log = logging.getLogger(__name__)

LOGIN_TTL = timedelta(minutes=15)
SESSION_SLIDE = timedelta(days=30)
# Sliding alone means an active session never dies, which makes uninviting
# someone impossible. The cap bounds it.
SESSION_CAP = timedelta(days=90)
OTP_ATTEMPTS = 5
EMAIL_PER_HOUR = 5
IP_PER_HOUR = 20

# check() runs on every proxied request via nginx auth_request — one DB read
# per HLS segment adds up. 60s of memory per cookie keeps steady-state
# streaming at ~one read per user per minute, and bounds how long a revoked
# session lingers.
_CHECK_TTL = 60.0
_check_cache: dict[str, tuple[str, float]] = {}


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite hands naive datetimes back; everything here compares in UTC.
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _allowed(email: str) -> bool:
    wanted = email.strip().lower()
    return wanted in {
        e.strip().lower() for e in settings.allowed_emails.split(",") if e.strip()
    }


def _over_limit(db: Session, key: str, cap: int) -> bool:
    """Count this request against `key`'s hourly bucket; True once past cap.

    Counted BEFORE the allowlist check, for every request — the limit exists
    for the strangers who never get a login row.
    """
    window = _now().replace(minute=0, second=0, microsecond=0)
    row = db.execute(
        select(AuthRate).where(AuthRate.key == key, AuthRate.window_start == window)
    ).scalar_one_or_none()
    if row is None:
        row = AuthRate(key=key, window_start=window, n=0)
        db.add(row)
    row.n += 1
    db.commit()
    return row.n > cap


def request_login(db: Session, email: str, ip: str) -> None:
    """Never raises, never varies. The inbox is the only observable outcome."""
    email = email.strip().lower()
    over_ip = _over_limit(db, f"ip:{ip}", IP_PER_HOUR)
    over_email = _over_limit(db, f"email:{email}", EMAIL_PER_HOUR)
    if over_ip or over_email or not _allowed(email):
        return

    token = secrets.token_urlsafe(32)
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        AuthLogin(
            email=email,
            token_hash=_hash(token),
            code_hash=_hash(code),
            created_at=_now(),
            expires_at=_now() + LOGIN_TTL,
        )
    )
    db.commit()

    link = f"{settings.public_origin.rstrip('/')}/api/auth/verify?token={token}"
    try:
        mail.send_login_email(email, link, code)
    except Exception:  # noqa: BLE001 — a downed Resend must not become an oracle
        log.exception("could not send login email")


def _create_session(db: Session, email: str, user_agent: str | None = None) -> str:
    sid = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            id_hash=_hash(sid),
            email=email,
            created_at=_now(),
            last_seen_at=_now(),
            expires_at=_now() + SESSION_SLIDE,
            user_agent=user_agent,
        )
    )
    db.commit()
    return sid


def verify_token(db: Session, token: str) -> str | None:
    """Consume a magic-link token; the returned value becomes the cookie."""
    row = db.execute(
        select(AuthLogin).where(AuthLogin.token_hash == _hash(token))
    ).scalar_one_or_none()
    if row is None or row.consumed_at is not None or _aware(row.expires_at) < _now():
        return None
    row.consumed_at = _now()
    db.commit()
    return _create_session(db, row.email)


def verify_otp(db: Session, email: str, code: str) -> str | None:
    email = email.strip().lower()
    row = db.execute(
        select(AuthLogin)
        .where(AuthLogin.email == email, AuthLogin.consumed_at.is_(None))
        .order_by(AuthLogin.id.desc())
    ).scalars().first()
    if row is None or _aware(row.expires_at) < _now() or row.attempts >= OTP_ATTEMPTS:
        return None
    if not secrets.compare_digest(row.code_hash, _hash(code)):
        row.attempts += 1
        db.commit()
        return None
    row.consumed_at = _now()
    db.commit()
    return _create_session(db, email)


def check(db: Session, cookie: str) -> str | None:
    """The gate. nginx subrequests this for every public request."""
    if not cookie:
        return None
    key = _hash(cookie)

    cached = _check_cache.get(key)
    now = time.monotonic()
    # None-check, not a 0.0 sentinel — monotonic counts from boot (see the
    # stream-heartbeat fix).
    if cached is not None and now - cached[1] < _CHECK_TTL:
        return cached[0]

    row = db.execute(
        select(AuthSession).where(AuthSession.id_hash == key)
    ).scalar_one_or_none()
    if row is None:
        return None
    if _aware(row.expires_at) < _now() or _aware(row.created_at) + SESSION_CAP < _now():
        return None
    # Revocation is an env edit: a session whose email left the allowlist is
    # dead within the cache TTL, no SQL required.
    if not _allowed(row.email):
        return None

    # Slide the expiry, throttled by the cache: this write happens at most
    # once per TTL per session.
    row.last_seen_at = _now()
    row.expires_at = _now() + SESSION_SLIDE
    db.commit()

    _check_cache[key] = (row.email, now)
    return row.email


def logout(db: Session, cookie: str) -> None:
    if not cookie:
        return
    key = _hash(cookie)
    db.execute(delete(AuthSession).where(AuthSession.id_hash == key))
    db.commit()
    _check_cache.pop(key, None)


def purge_expired(db: Session) -> int:
    """Janitor: drop dead logins, sessions and stale rate buckets."""
    cutoff = _now()
    n = 0
    n += db.execute(delete(AuthLogin).where(AuthLogin.expires_at < cutoff)).rowcount
    n += db.execute(delete(AuthSession).where(AuthSession.expires_at < cutoff)).rowcount
    n += db.execute(
        delete(AuthRate).where(AuthRate.window_start < cutoff - timedelta(hours=2))
    ).rowcount
    db.commit()
    return n
