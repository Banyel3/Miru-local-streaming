"""Shared fixtures.

The whole suite runs against SQLite in memory: no Postgres, no ffmpeg, no
second machine. Anything that would reach out is faked at the seam, so a test
failure means Miru is broken rather than that something was not running.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from miru.core.db import Base, get_db
from miru.library.models import MediaFile


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one connection, so :memory: survives across sessions
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as s:
        yield s


@pytest.fixture
def client(db_session, monkeypatch):
    """API client with the database swapped and the transcode worker absent.

    The worker is forced offline by default so tests describe the common case —
    a laptop-only deployment — and opt in to the PC being up when that is what
    they are actually testing.
    """
    from miru.core.config import settings
    import miru.transcode.worker as worker_mod
    from miru.main import app

    monkeypatch.setattr(settings, "transcode_worker", "")
    monkeypatch.setattr(worker_mod, "_last_checked", 0.0)

    app.dependency_overrides[get_db] = lambda: db_session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def worker_offline(monkeypatch):
    """A worker is configured, but the PC is asleep.

    Distinct from the default fixture, where no worker is configured at all.
    The two produce different messages because they are different problems: one
    is 'turn the PC on', the other is 'you never set this up'.
    """
    from miru.core.config import settings
    import miru.transcode.worker as worker_mod

    monkeypatch.setattr(settings, "transcode_worker", "http://pc:8010")
    monkeypatch.setattr(worker_mod, "worker_up", lambda: False)


@pytest.fixture
def worker_online(monkeypatch):
    """Pretend the PC is up and its worker answering."""
    from miru.core.config import settings
    import miru.transcode.worker as worker_mod

    monkeypatch.setattr(settings, "transcode_worker", "http://pc:8010")
    monkeypatch.setattr(settings, "public_api_url", "http://laptop:8000")
    monkeypatch.setattr(worker_mod, "worker_up", lambda: True)


def make_file(db, **kw) -> MediaFile:
    """A media_files row with sane defaults — override only what the test is about."""
    defaults = dict(
        path="/mnt/storage/media/Show/Show - S01E01.mp4",
        title="Show - S01E01",
        size_bytes=1_400_000_000,
        mtime=1_700_000_000.0,
        duration_ms=1_440_000,
        container="mp4",
        video_codec="h264",
        audio_codec="aac",
        audio_channels=2,
        width=1920,
        height=1080,
        subtitle_streams=[],
        playback_strategy="direct",
    )
    f = MediaFile(**{**defaults, **kw})
    db.add(f)
    db.commit()
    return f
