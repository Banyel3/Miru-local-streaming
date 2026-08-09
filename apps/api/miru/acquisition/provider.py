"""Acquisition boundary — spec §9.

The interface, and the shapes that cross it. Implementations live beside this
file. Miru talks to acquisition services over HTTP only and shares no database
models with them; completed downloads land in the incoming directory and the
scanner picks them up like any other file.

Deviation from the spec, recorded deliberately: §9 declares these methods
`async`. They are sync here. Search is one outbound HTTP call and aria2's RPC is
another; FastAPI already runs sync endpoints in a threadpool, so nothing blocks
the event loop, and staying sync avoids adding an async HTTP client to the
dependency list for two calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class AcquisitionError(RuntimeError):
    """Anything that went wrong past this boundary.

    One type on purpose: callers turn it into a 502 and a sentence, and they
    should not have to know which downloader is configured to do that.
    """


@dataclass
class SearchResult:
    """One release, from whichever indexer found it.

    Carries seeders and size because they are how a person actually chooses
    between two hundred results for the same episode, and the ids because M2's
    metadata matching will want them rather than re-deriving them from a
    filename.
    """

    id: str
    title: str
    indexer: str
    size_bytes: int
    seeders: int
    leechers: int
    age_days: int
    magnet: str | None
    download_url: str | None
    # The torrent's own identity, and the only field here that is stable.
    # Prowlarr re-encrypts its download links on every response, so guid and
    # downloadUrl change between two calls three seconds apart — measured at
    # 299 of 299 results. Identity has to come from the swarm, not the proxy.
    info_hash: str | None = None
    # When the indexer says the release was published. Present on every result
    # measured, and the only field that answers "what is new" — first_seen_at
    # only answers "when did Miru last look", which is the same instant for
    # everything in a pass and therefore sorts as noise.
    published_at: str | None = None
    categories: list[str] = field(default_factory=list)
    # Ids, not just names. Classification runs on these because the live
    # instance returns category objects carrying an id and no name at all
    # ({"id": 131088}), so a name-based rule drops them silently.
    category_ids: list[int] = field(default_factory=list)
    imdb_id: str | None = None
    tmdb_id: int | None = None
    tvdb_id: int | None = None

    @property
    def grabbable(self) -> bool:
        """A result with no magnet and no torrent URL cannot be acted on, and
        showing it as a choice is a promise the UI cannot keep."""
        return bool(self.magnet or self.download_url)


@dataclass
class DownloadJob:
    id: str
    result_id: str


@dataclass
class DownloadStatus:
    id: str
    state: str  # queued | downloading | done | failed | cancelled
    progress: float  # 0.0 to 1.0
    name: str | None = None
    downloaded_bytes: int = 0
    total_bytes: int = 0
    speed_bps: int = 0
    eta_seconds: int | None = None
    error: str | None = None


class AcquisitionProvider(Protocol):
    def search(self, query: str, limit: int = 50) -> list[SearchResult]: ...
    def submit(self, result_id: str) -> DownloadJob: ...
    def status(self, job_id: str) -> DownloadStatus: ...
    def cancel(self, job_id: str, delete_files: bool = False) -> None: ...
