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
    aria2_secret: str = ""

    # Transcode worker on the PC. Empty means no worker: files needing an
    # encoder are then reported unavailable rather than failing at play time.
    transcode_worker: str = ""

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
