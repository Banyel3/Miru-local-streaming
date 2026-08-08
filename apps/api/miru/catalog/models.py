"""Catalog tables.

The catalog *accumulates*. Measured on the live instance, Prowlarr exposes no
pagination — `offset=100` returns nothing — so one refresh can only ever see the
indexers' current front page, about 300 items. Depth cannot come from asking
harder, only from asking regularly and keeping what came back. Rows are
therefore never deleted on refresh: day one has a few hundred, a fortnight in it
has thousands, and the wall gets deeper with age instead of decaying.

That also settles the store. A cache with eviction would delete exactly the
history that gives the wall its depth, and the read pattern is keyset pagination
over sorted filtered rows, which is a `WHERE ... ORDER BY ... LIMIT` and not a
hand-maintained sorted set. See the plan, §6.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from miru.core.db import Base

# Same variant trick as library.models: JSONB on Postgres, plain JSON on
# SQLite, so the suite runs in CI with no database server.
JSONType = JSON().with_variant(JSONB(), "postgresql")


# Public trackers, for bootstrap only. DHT alone finds peers eventually; these
# make it seconds rather than minutes, which is the difference between Watch Now
# feeling instant and feeling broken.
OPEN_TRACKERS = (
    "udp://tracker.opentrackr.org:1337/announce",
    "udp://open.demonii.com:1337/announce",
    "udp://tracker.torrent.eu.org:451/announce",
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CatalogWork(Base):
    """One thing you would actually watch.

    A card on the wall is a work, not a release. The top of the anime list by
    seeders is four separate rows for the same show, so a wall built from
    releases is a wall of One Piece.

    Identity is the metadata provider's id once a title resolves to one, and the
    parsed title only until then. Measured on the live catalogue, 21 title
    prefixes covered 47 separate works — *Youjo Senki 幼女戦記*, *Saga of Tanya
    the Evil* and *Youjo Senki 幼女戦記 Movie* share no characters, so no amount
    of string cleaning merges them. AniList already knows all three are 21613.
    """

    __tablename__ = "catalog_works"
    __table_args__ = (
        UniqueConstraint("kind", "normalised_title", "year", name="uq_work_identity"),
        # What makes one card out of romaji, English and native script. NULLs
        # compare distinct in both Postgres and SQLite, so the unresolved works
        # — which is most of them on day one — are unaffected.
        # Without `kind`: the kind is derived from the provider now, not an
        # independent fact, and keeping it here is what let one show hold
        # anilist/154587 and tvmaze/69956 at once. TMDB ids carry a
        # `movie:`/`tv:` namespace because TMDB numbers the two separately.
        UniqueConstraint("provider", "provider_id", name="uq_work_provider"),
        Index("ix_works_kind_seeders", "kind", "best_seeder_pct"),
        Index("ix_works_kind_first_seen", "kind", "first_seen_at"),
        Index("ix_works_kind_latest", "kind", "latest_release_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # anime | movie | series
    normalised_title: Mapped[str] = mapped_column(String(512))
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    display_title: Mapped[str] = mapped_column(String(512))

    # Enrichment (M5b). Nullable throughout, and never blocking ingest: a slow
    # or unconfigured TMDB must not stop the wall from filling.
    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    overview: Mapped[str | None] = mapped_column(String, nullable=True)
    poster_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    backdrop_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    genres: Mapped[list] = mapped_column(JSONType, default=list)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    # AniList's own word: TV | MOVIE | OVA | ONA | SPECIAL. This is what tells a
    # film from a weekly show inside the anime rail — *One Piece Film: Red* is
    # MOVIE, the series is TV — so the wall never needs a fifth filter pill.
    format: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # What the provider says the whole run is, against the handful of episodes
    # the catalogue actually holds. "8 of 1,172" is the honest line.
    episode_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Denormalised so a rail is one indexed query rather than a join per row.
    best_seeder_pct: Mapped[float] = mapped_column(Float, default=0.0)
    release_count: Mapped[int] = mapped_column(Integer, default=0)
    # Newest publication date among this work's releases. What "latest" means on
    # the wall, and the reason the first rail is ordered rather than arbitrary.
    latest_release_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Written at download time rather than derived. Deriving it needs both sides
    # normalised through the same parser, and until that exists the in-library
    # state would simply never fire and every show you own would sit on the wall
    # offering to download itself again.
    library_file_id: Mapped[int | None] = mapped_column(
        ForeignKey("media_files.id", ondelete="SET NULL"), nullable=True
    )
    download_job_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CatalogRelease(Base):
    """One release as seen at one indexer."""

    __tablename__ = "catalog_releases"
    __table_args__ = (
        # The infohash, because it is the only stable thing on offer. Prowlarr
        # re-encrypts its download links every response — measured, 299 of 299
        # guids differed between two calls three seconds apart — so keying on
        # guid would have inserted the entire catalogue again on every refresh
        # and quietly destroyed the accumulate design.
        #
        # Not scoped to the indexer either: the same torrent listed on two
        # indexers is one torrent, and collapsing it is correct rather than a
        # compromise.
        UniqueConstraint("info_hash", name="uq_release_identity"),
        Index("ix_releases_work", "work_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    info_hash: Mapped[str] = mapped_column(String(40))
    indexer: Mapped[str] = mapped_column(String(128))
    # Last seen link. Kept for reference only: it expires, and grabs are built
    # from the infohash instead.
    guid: Mapped[str] = mapped_column(Text)

    title: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), index=True)
    work_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_works.id", ondelete="CASCADE"), nullable=True
    )

    parsed_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    season: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode: Mapped[int | None] = mapped_column(Integer, nullable=True)
    episode_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    quality: Mapped[str | None] = mapped_column(String(16), nullable=True)
    release_group: Mapped[str | None] = mapped_column(String(128), nullable=True)

    size_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    seeders: Mapped[int] = mapped_column(Integer, default=0)
    leechers: Mapped[int] = mapped_column(Integer, default=0)
    # Standing within this release's own indexer, not a raw count. Raw counts
    # are not comparable across indexers: one reports a max of 303, another
    # reports zero for two thirds of its catalogue, a third caps out at 2.
    seeder_pct: Mapped[float] = mapped_column(Float, default=0.5)

    magnet: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    imdb_id: Mapped[str | None] = mapped_column(String(16), nullable=True)
    categories: Mapped[list] = mapped_column(JSONType, default=list)

    # Predicted from the release name, not probed. The claim the picker makes
    # about whether grabbing this means waking the PC.
    predicted_strategy: Mapped[str] = mapped_column(String(24), default="direct")

    # Not seen in the last N refreshes. Kept, because a row is still a true
    # statement about what existed, but never recommended: a magnet stored in
    # August may be dead in November, and a wall that never deletes would
    # otherwise fill with links that fail on click.
    missed_refreshes: Mapped[int] = mapped_column(Integer, default=0)

    # The indexer's own publication date. Distinct from first_seen_at, which is
    # only when Miru last looked — identical for everything in a pass, so it
    # sorts as noise rather than as recency.
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    STALE_AFTER = 20

    @property
    def magnet_uri(self) -> str:
        """A magnet built from the infohash, not from the stored link.

        Prowlarr's proxy URL carries an encrypted payload it regenerates each
        response, so a link stored weeks ago is not something to rely on. An
        infohash magnet is what the swarm actually answers to, and both DHT and
        the trackers below will find peers from it.
        """
        import urllib.parse

        trackers = "".join(f"&tr={urllib.parse.quote(t, safe='')}" for t in OPEN_TRACKERS)
        return (
            f"magnet:?xt=urn:btih:{self.info_hash}"
            f"&dn={urllib.parse.quote(self.title)}{trackers}"
        )

    @property
    def stale(self) -> bool:
        return self.missed_refreshes >= self.STALE_AFTER

    @property
    def grabbable(self) -> bool:
        return bool(self.magnet or self.download_url)


class TitleResolution(Base):
    """One parsed title, and the provider work it turned out to be.

    Keyed on the normalised *parsed* title, so the cost is one lookup per
    distinct name rather than one per release: 193 anime releases in the live
    catalogue are well under a hundred names, asked once. New episodes of a
    known show then cost nothing at all, which matters because AniList allows 90
    requests a minute and a refresh must not sit waiting on it.

    A row with no provider is a remembered *miss*. Without it the untraceable
    releases — and there are always some — get asked about again every pass,
    forever.
    """

    __tablename__ = "catalog_title_resolutions"
    # Keyed on the query alone. Under (kind, query) the identical title was
    # asked about twice — once as anime, once as series — and could be answered
    # differently each time, which is how one show ended up with two ids.
    __table_args__ = (UniqueConstraint("query", name="uq_resolution_query"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    query: Mapped[str] = mapped_column(String(512))

    provider: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # The whole provider payload. A blob rather than columns because nothing
    # queries it — it is read by primary key and copied onto the work — and a
    # provider growing a field should not mean a migration.
    data: Mapped[dict] = mapped_column(JSONType, default=dict)

    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CatalogRefresh(Base):
    """One refresh pass, recorded so a dead one is visible.

    The whole design assumes the refresh keeps running. If it dies the wall does
    not break, it just stops getting deeper, which nobody notices for weeks.
    That is a silent failure, so the UI reads this table for its "updated N
    minutes ago" line and a failure shows up on the wall itself rather than in a
    log nobody opens.
    """

    __tablename__ = "catalog_refreshes"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    seen: Mapped[int] = mapped_column(Integer, default=0)
    added: Mapped[int] = mapped_column(Integer, default=0)
    per_indexer: Mapped[dict] = mapped_column(JSONType, default=dict)
    error: Mapped[str | None] = mapped_column(String(512), nullable=True)
