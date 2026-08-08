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
    aria2_url: str = ""
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
