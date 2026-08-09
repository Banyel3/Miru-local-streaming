from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from miru.auth import service
from miru.core.db import get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])

COOKIE = "miru_session"


def _set_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        COOKIE,
        sid,
        max_age=int(service.SESSION_SLIDE.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def _client_ip(request: Request) -> str:
    # Funnel traffic reaches nginx from loopback with the real address in
    # X-Forwarded-For (set by tailscaled). Trust the header only from there —
    # anything else could just write its own.
    host = request.client.host if request.client else ""
    if host in ("127.0.0.1", "::1"):
        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return host or "unknown"


class LoginRequest(BaseModel):
    email: str


class OtpRequest(BaseModel):
    email: str
    code: str


@router.post("/request")
def request_login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Identical answer for every input — the inbox is the only oracle."""
    service.request_login(db, body.email, ip=_client_ip(request))
    return {"ok": True}


@router.get("/verify")
def verify(token: str = "", db: Session = Depends(get_db)):
    sid = service.verify_token(db, token)
    if sid is None:
        # The login page owns the copy for this; a bare 401 on a clicked link
        # is a dead end with no way forward.
        return RedirectResponse("/login?error=expired", status_code=302)
    response = RedirectResponse("/", status_code=302)
    _set_cookie(response, sid)
    return response


@router.post("/otp")
def otp(body: OtpRequest, response: Response, db: Session = Depends(get_db)):
    sid = service.verify_otp(db, body.email, body.code)
    if sid is None:
        response.status_code = 401
        return {"ok": False}
    _set_cookie(response, sid)
    return {"ok": True}


@router.get("/check")
def check(request: Request, db: Session = Depends(get_db)):
    """The nginx auth_request target: 204 in, 401 out. Body-less by design."""
    email = service.check(db, request.cookies.get(COOKIE, ""))
    return Response(status_code=204 if email else 401)


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    service.logout(db, request.cookies.get(COOKIE, ""))
    response = Response(status_code=204)
    response.delete_cookie(COOKIE, path="/")
    return response
