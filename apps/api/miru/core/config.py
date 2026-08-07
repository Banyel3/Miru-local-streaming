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

    web_origin: str = "http://localhost:3000"

    # Extracted subtitles land here. Rebuildable from the source files, so it
    # is a cache and not data: deleting it costs one ffmpeg run per track.
    cache_dir: str = ".cache"

    @property
    def cache(self) -> Path:
        return Path(self.cache_dir).resolve()

    @property
    def libraries(self) -> list[Path]:
        return [Path(p) for p in self.library_paths.split(":") if p.strip()]


settings = Settings()
