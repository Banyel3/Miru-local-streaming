import pytest


class TestAFailedStartDoesNotLeakASession:
    """Measured on the live worker while the nv12 bug was unfixed: three failed
    requests left `active_sessions: 3` of a maximum 4. The fourth failure would
    have made the worker refuse ALL new work — including requests that would
    have succeeded — so a fixable encoder bug would have presented as the whole
    machine being broken, intermittently, depending on how many times you had
    tried before.
    """

    def test_a_dead_session_does_not_hold_a_slot(self, monkeypatch):
        """The real shape of it. A session whose ffmpeg has exited stays in the
        table until session_ttl_hours (12h) elapses, so it keeps counting toward
        max_sessions. Four dead sessions and the worker refuses everything for
        half a day."""
        from miru_worker import sessions as S

        class Dead:
            def poll(self):
                return 1

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 1

        monkeypatch.setattr(S, "_sessions", {})
        for i in range(4):
            S._sessions[f"s{i}"] = S.Session(
                sid=f"s{i}", src=f"http://x/{i}", dir=None, renditions=[], process=Dead()
            )

        # None of them is alive, so none of them should be occupying a slot.
        assert len(S.active()) == 0, (
            "dead sessions still count toward max_sessions, so a run of "
            "failures makes the worker refuse work that would succeed"
        )

    def test_a_session_that_never_produces_a_manifest_is_removed(self, monkeypatch, tmp_path):
        import asyncio

        from miru_worker import sessions as S

        monkeypatch.setattr(S, "_sessions", {})
        monkeypatch.setattr(S.settings, "cache_dir", str(tmp_path))
        monkeypatch.setattr(S.settings, "startup_timeout_s", 0.2)

        class DeadProc:
            returncode = 1
            stderr = None

            def poll(self):
                return 1

            def kill(self):
                pass

            def wait(self, timeout=None):
                return 1

        monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: DeadProc())

        with pytest.raises(Exception):
            asyncio.run(S.ensure_session("http://laptop:8000/api/stream/1", 1080, False))

        assert S._sessions == {}, (
            "a session that failed to start is still registered, so repeated "
            "failures fill max_sessions and the worker stops accepting work"
        )
