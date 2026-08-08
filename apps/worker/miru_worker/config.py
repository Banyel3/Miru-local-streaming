from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WORKER_", env_file=".env", extra="ignore")

    # HLS output. Local disk on the PC, deliberately: a two-hour film at 6 Mbps
    # is ~5 GB, which fits neither tmpfs nor a wifi-backed NFS share.
    cache_dir: str = ".cache/hls"

    # The worker fetches whatever URL it is handed. Without this it is an SSRF
    # hole reachable by anything on the tailnet. Comma-separated prefixes.
    allowed_source_prefixes: str = "http://localhost:8000/,http://127.0.0.1:8000/"

    # Encoder override. Empty means detect: NVENC when the device really works,
    # x264 otherwise — which is what lets this run on a machine with no GPU.
    encoder: str = ""

    # hls.js fetches manifests and segments by XHR, so the worker is a
    # cross-origin fetch target and needs CORS. A bare <video src> would not,
    # which is why this only shows up once HLS enters the picture.
    web_origin: str = "http://localhost:3001"   # comma-separated

    max_sessions: int = 4
    session_ttl_hours: int = 12

    # How long a first request waits for ffmpeg to produce a playable manifest.
    startup_timeout_s: float = 45.0

    @property
    def web_origins(self) -> list[str]:
        return [o.strip() for o in self.web_origin.split(",") if o.strip()]

    @property
    def cache(self) -> Path:
        return Path(self.cache_dir).resolve()

    @property
    def allowed_prefixes(self) -> list[str]:
        return [p.strip() for p in self.allowed_source_prefixes.split(",") if p.strip()]


settings = Settings()
