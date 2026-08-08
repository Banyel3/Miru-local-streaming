from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MIRU_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://miru:miru@localhost/miru"

    # Colon-separated so it reads like PATH: /mnt/d/Anime:/mnt/e/Film
    library_paths: str = ""

    # Empty means the API is open. Correct for localhost, wrong once Tailscale
    # can reach it — see docs/SETUP.md §8.
    token: str = ""

    # Comma-separated. You browse from localhost on the server itself and from
    # the tailnet address on every other device, and both are real origins.
    web_origin: str = "http://localhost:3001"

    # Extracted subtitles land here. Rebuildable from the source files, so it
    # is a cache and not data: deleting it costs one ffmpeg run per track.
    cache_dir: str = ".cache"

    # Where the downloader writes. Deliberately NOT scanned: a growing file
    # probes as garbage and, because the scan also records size and mtime, is
    # never re-probed. Entries move into the library once they stop changing.
    incoming_path: str = ""
    incoming_settle_seconds: float = 120.0

    # Acquisition, both on the PC. Empty means the feature is off rather than
    # broken: the API reports it unconfigured instead of failing at search time.
    prowlarr_url: str = ""
    prowlarr_api_key: str = ""
    # Which downloader actually runs. qBittorrent is the default because it is
    # the only one of the two that can be watched while it is still arriving:
    # aria2 has no sequential download for BitTorrent at all. Set to "aria2" to
    # fall back — one environment variable rather than a migration.
    downloader: str = "qbittorrent"

    qbittorrent_url: str = ""
    qbittorrent_user: str = "admin"
    qbittorrent_password: str = ""
    # Left empty so qBittorrent's own configured save path wins, the same way
    # aria2's does. Miru never names a directory that lives on another machine.
    qbittorrent_save_path: str = ""

    aria2_url: str = ""
    # How often to ask the indexers what exists. Zero disables the loop, which
    # is what the test suite and CI run with.
    catalog_refresh_seconds: int = 1800

    # Extra searches run on every refresh, alongside the empty browse query.
    #
    # Browse returns each indexer's front page, which is whatever the world is
    # downloading — so anything regional is invisible on the wall no matter how
    # much of it exists. Measured: "tagalog" returns 257 results and "filipino"
    # 251, and none of them appear in an unfiltered browse. Naming what you
    # actually want is the only way it reaches the rails.
    catalog_queries: str = ""

    # Film artwork. AniList (anime) and TVmaze (series) need no key; TMDB is
    # the only source with real film coverage and the only one that does.
    # Without it, films fall back to generated title cards rather than breaking.
    tmdb_api_key: str = ""
    poster_cache_path: str = ""

    # Playable copies of MKV files. Stream-copy only, so these are cheap to make
    # and cheap to lose; the cache is capped because a remux is the same size as
    # its source and an unbounded one would double the library.
    remux_cache_path: str = ""
    remux_cache_bytes: int = 40 * 1024 * 1024 * 1024
    aria2_secret: str = ""

    # Transcode worker on the PC. Empty means no worker: files needing an
    # encoder are then reported unavailable rather than failing at play time.
    transcode_worker: str = ""

    # Where the BROWSER should reach the worker. Empty falls back to
    # transcode_worker, which is the direct cross-origin address and works only
    # without a reverse proxy. Behind nginx this is a same-origin path, which is
    # what stops the 302 from tainting Origin to `null`.
    public_worker_url: str = ""

    # How the *worker* reaches this API to pull sources. Must be an address the
    # PC can resolve — not localhost, which would point it at itself.
    public_api_url: str = "http://localhost:8000"

    @property
    def web_origins(self) -> list[str]:
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]

    @property
    def cache(self) -> Path:
        return Path(self.cache_dir).resolve()

    @property
    def incoming(self) -> Path | None:
        return Path(self.incoming_path) if self.incoming_path.strip() else None

    @property
    def libraries(self) -> list[Path]:
        return [Path(p) for p in self.library_paths.split(":") if p.strip()]


settings = Settings()
