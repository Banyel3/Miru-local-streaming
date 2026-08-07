import secrets

from fastapi import HTTPException, Request

from miru.core.config import settings


def require_token(request: Request) -> None:
    """Single-user auth. Tailscale is the real perimeter; this stops a
    misconfigured port from being an open library."""
    if not settings.token:
        return

    header = request.headers.get("authorization", "")
    supplied = header.removeprefix("Bearer ").strip() or request.cookies.get("miru_token", "")

    if not secrets.compare_digest(supplied, settings.token):
        raise HTTPException(status_code=401, detail="bad or missing token")
