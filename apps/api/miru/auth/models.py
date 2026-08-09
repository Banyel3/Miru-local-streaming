"""Who may enter, and who is currently in.

Three tables, one job each:

    auth_logins    a pending invitation to prove an email — one magic-link
                   token and one OTP code, 15 minutes, single use
    auth_sessions  a browser that proved one — the cookie's server half
    auth_rate      how often each email and IP has asked — counted for
                   EVERY request, allowlisted or not, because the limit
                   exists for the strangers who never get a login row
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from miru.core.db import Base


class AuthLogin(Base):
    __tablename__ = "auth_logins"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String, index=True)
    token_hash: Mapped[str] = mapped_column(String, unique=True)
    code_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # The cookie value is never stored — only its sha256, so a database read
    # is not a bag of usable cookies.
    id_hash: Mapped[str] = mapped_column(String, unique=True, index=True)
    email: Mapped[str] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)


class AuthRate(Base):
    __tablename__ = "auth_rate"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # "email:<addr>" or "ip:<addr>", bucketed by hour.
    key: Mapped[str] = mapped_column(String, index=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    n: Mapped[int] = mapped_column(Integer, default=0)
