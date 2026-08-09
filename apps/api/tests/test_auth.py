"""Go-live auth: magic link + OTP, allowlist, sessions.

Written before the implementation (docs/plans/go-live-auth.md). The public
gate is nginx auth_request against /api/auth/check; everything here is the
API behind that gate. Resend is faked at the seam — a test that emails a
real person is a bug.
"""

from datetime import datetime, timedelta, timezone

import pytest

from miru.auth import service
from miru.auth.models import AuthLogin, AuthRate, AuthSession


SENT: list[dict] = []


@pytest.fixture
def auth_env(monkeypatch, db_session):
    """Allowlist of two, mail captured instead of sent, caches empty."""
    from miru.core.config import settings

    monkeypatch.setattr(settings, "allowed_emails", "friend@example.com, Other@Example.COM")
    monkeypatch.setattr(settings, "public_origin", "https://ban-1.tail88f195.ts.net")
    SENT.clear()
    monkeypatch.setattr(
        service.mail, "send_login_email", lambda email, link, code: SENT.append(
            {"email": email, "link": link, "code": code}
        )
    )
    monkeypatch.setattr(service, "_check_cache", {})
    return db_session


def _rows(db, model):
    from sqlalchemy import select

    return list(db.execute(select(model)).scalars())


class TestRequestLogin:
    def test_allowlisted_email_gets_a_row_and_a_mail(self, auth_env):
        service.request_login(auth_env, "friend@example.com", ip="1.2.3.4")
        rows = _rows(auth_env, AuthLogin)
        assert len(rows) == 1
        assert SENT and SENT[0]["email"] == "friend@example.com"
        assert SENT[0]["link"].startswith("https://ban-1.tail88f195.ts.net/api/auth/verify?token=")
        assert len(SENT[0]["code"]) == 6

    def test_unknown_email_no_row_no_mail_same_outcome(self, auth_env):
        # The response is the router's job; the service must simply do nothing
        # observable. No login row, no mail — and no exception to turn into a
        # different status code.
        service.request_login(auth_env, "stranger@example.com", ip="1.2.3.4")
        assert _rows(auth_env, AuthLogin) == []
        assert SENT == []

    def test_emails_compare_case_insensitively(self, auth_env):
        service.request_login(auth_env, "OTHER@example.com", ip="1.2.3.4")
        assert len(_rows(auth_env, AuthLogin)) == 1

    def test_mail_failure_does_not_raise(self, auth_env, monkeypatch):
        # A downed Resend must not become a 500 — a different status for
        # allowlisted addresses would be an allowlist oracle.
        def boom(email, link, code):
            raise RuntimeError("resend down")

        monkeypatch.setattr(service.mail, "send_login_email", boom)
        service.request_login(auth_env, "friend@example.com", ip="1.2.3.4")


class TestRateLimit:
    def test_sixth_request_in_an_hour_sends_nothing(self, auth_env):
        for _ in range(6):
            service.request_login(auth_env, "friend@example.com", ip="1.2.3.4")
        assert len(_rows(auth_env, AuthLogin)) == 5
        assert len(SENT) == 5

    def test_probe_traffic_is_counted_even_when_not_allowlisted(self, auth_env):
        # The limit exists FOR strangers probing the endpoint. Counting only
        # allowlisted requests (login rows) would exempt exactly them.
        for _ in range(3):
            service.request_login(auth_env, "stranger@example.com", ip="9.9.9.9")
        rates = _rows(auth_env, AuthRate)
        assert any(r.key == "ip:9.9.9.9" and r.n == 3 for r in rates)

    def test_ip_cap_blocks_at_twenty(self, auth_env):
        for i in range(25):
            service.request_login(auth_env, f"stranger{i}@example.com", ip="9.9.9.9")
        service.request_login(auth_env, "friend@example.com", ip="9.9.9.9")
        assert SENT == []  # the shared IP burned the budget; neutral outcome


class TestVerify:
    def _request(self, db):
        service.request_login(db, "friend@example.com", ip="1.2.3.4")
        return SENT[-1]

    def test_token_happy_path_creates_a_session(self, auth_env):
        mail = self._request(auth_env)
        token = mail["link"].rsplit("token=", 1)[-1]
        sid = service.verify_token(auth_env, token)
        assert sid
        sessions = _rows(auth_env, AuthSession)
        assert len(sessions) == 1 and sessions[0].email == "friend@example.com"

    def test_token_is_single_use(self, auth_env):
        token = self._request(auth_env)["link"].rsplit("token=", 1)[-1]
        assert service.verify_token(auth_env, token)
        assert service.verify_token(auth_env, token) is None

    def test_token_expires(self, auth_env):
        token = self._request(auth_env)["link"].rsplit("token=", 1)[-1]
        row = _rows(auth_env, AuthLogin)[0]
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        auth_env.commit()
        assert service.verify_token(auth_env, token) is None

    def test_otp_happy_path(self, auth_env):
        code = self._request(auth_env)["code"]
        assert service.verify_otp(auth_env, "friend@example.com", code)

    def test_otp_five_wrong_attempts_kill_the_row(self, auth_env):
        code = self._request(auth_env)["code"]
        for _ in range(5):
            assert service.verify_otp(auth_env, "friend@example.com", "000000") is None
        # Even the right code is dead now — a guesser must not get unlimited tries.
        assert service.verify_otp(auth_env, "friend@example.com", code) is None


class TestCheck:
    def _session(self, db) -> str:
        service.request_login(db, "friend@example.com", ip="1.2.3.4")
        token = SENT[-1]["link"].rsplit("token=", 1)[-1]
        return service.verify_token(db, token)

    def test_valid_cookie_yields_the_email(self, auth_env):
        sid = self._session(auth_env)
        assert service.check(auth_env, sid) == "friend@example.com"

    def test_missing_and_garbage_cookies_fail(self, auth_env):
        assert service.check(auth_env, "") is None
        assert service.check(auth_env, "not-a-session") is None

    def test_expired_session_fails(self, auth_env):
        sid = self._session(auth_env)
        row = _rows(auth_env, AuthSession)[0]
        row.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        auth_env.commit()
        service._check_cache.clear()
        assert service.check(auth_env, sid) is None

    def test_ninety_day_absolute_cap(self, auth_env):
        # Sliding expiry alone means an active session never dies, which makes
        # uninviting someone impossible.
        sid = self._session(auth_env)
        row = _rows(auth_env, AuthSession)[0]
        row.created_at = datetime.now(timezone.utc) - timedelta(days=91)
        auth_env.commit()
        service._check_cache.clear()
        assert service.check(auth_env, sid) is None

    def test_allowlist_removal_revokes_a_live_session(self, auth_env, monkeypatch):
        from miru.core.config import settings

        sid = self._session(auth_env)
        assert service.check(auth_env, sid) == "friend@example.com"
        monkeypatch.setattr(settings, "allowed_emails", "other@example.com")
        service._check_cache.clear()
        assert service.check(auth_env, sid) is None

    def test_logout_deletes_the_session(self, auth_env):
        sid = self._session(auth_env)
        service.logout(auth_env, sid)
        service._check_cache.clear()
        assert service.check(auth_env, sid) is None
        assert _rows(auth_env, AuthSession) == []


class TestRoutes:
    def test_request_returns_identical_200_for_both(self, client, auth_env):
        a = client.post("/api/auth/request", json={"email": "friend@example.com"})
        b = client.post("/api/auth/request", json={"email": "stranger@example.com"})
        assert a.status_code == b.status_code == 200
        assert a.json() == b.json()

    def test_verify_sets_cookie_and_redirects_home(self, client, auth_env):
        client.post("/api/auth/request", json={"email": "friend@example.com"})
        token = SENT[-1]["link"].rsplit("token=", 1)[-1]
        r = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/"
        assert "miru_session=" in r.headers.get("set-cookie", "")

    def test_bad_token_redirects_to_login_with_error(self, client, auth_env):
        r = client.get("/api/auth/verify?token=junk", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == "/login?error=expired"

    def test_check_route_204_and_401(self, client, auth_env):
        assert client.get("/api/auth/check").status_code == 401
        client.post("/api/auth/request", json={"email": "friend@example.com"})
        token = SENT[-1]["link"].rsplit("token=", 1)[-1]
        r = client.get(f"/api/auth/verify?token={token}", follow_redirects=False)
        # The cookie is Secure and the test client is plain http, so the jar
        # (rightly) refuses it — carry it by hand, as nginx will.
        sid = r.headers["set-cookie"].split("miru_session=", 1)[1].split(";", 1)[0]
        assert client.get("/api/auth/check", cookies={"miru_session": sid}).status_code == 204


class TestPurge:
    def test_purge_drops_dead_rows_and_keeps_live_ones(self, auth_env):
        service.request_login(auth_env, "friend@example.com", ip="1.2.3.4")
        token = SENT[-1]["link"].rsplit("token=", 1)[-1]
        sid = service.verify_token(auth_env, token)
        # Age the login row past its TTL; the session stays live.
        row = _rows(auth_env, AuthLogin)[0]
        row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        auth_env.commit()
        n = service.purge_expired(auth_env)
        assert n >= 1
        assert _rows(auth_env, AuthLogin) == []
        assert len(_rows(auth_env, AuthSession)) == 1
        assert service.check(auth_env, sid) == "friend@example.com"
