from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from miru.core.db import Base

# M1 tables only. series / episodes / genres / progress / favourites arrive
# with the metadata module in M2 — see ARCHITECTURE.md §3.


class MediaFile(Base):
    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String, unique=True, index=True)
    title: Mapped[str] = mapped_column(String)  # filename stem until M2 parses it
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[float]

    duration_ms: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    container: Mapped[str | None] = mapped_column(String, nullable=True)
    video_codec: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_codec: Mapped[str | None] = mapped_column(String, nullable=True)
    audio_channels: Mapped[int | None] = mapped_column(Integer, nullable=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subtitle_streams: Mapped[list] = mapped_column(JSONB, default=list)

    playback_strategy: Mapped[str] = mapped_column(String, default="direct")
    probed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    type: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending|running|done|failed
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
