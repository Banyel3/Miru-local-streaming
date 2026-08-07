"""Acquisition boundary — ARCHITECTURE.md, spec §9.

Interface only. No providers until M6, and the Internet Archive adapter is the
only one that gets built: public API, public-domain collection, legitimately
downloadable, and enough to prove the protocol holds.

An acquisition service talks to Miru over HTTP and never shares database
models with it. Completed downloads land in the library directory and the
scanner picks them up like any other file — that is the entire integration.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass
class SearchResult:
    id: str
    title: str
    size_bytes: int | None
    source: str


@dataclass
class DownloadJob:
    id: str
    result_id: str


@dataclass
class DownloadStatus:
    id: str
    state: str  # queued | downloading | done | failed
    progress: float
    error: str | None = None


class AcquisitionProvider(Protocol):
    async def search(self, query: str) -> list[SearchResult]: ...
    async def submit(self, result_id: str) -> DownloadJob: ...
    async def status(self, job_id: str) -> DownloadStatus: ...
    async def cancel(self, job_id: str) -> None: ...
