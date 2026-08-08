

class TestSubtitleExtractionActuallyRuns:
    """Written before the fix. Both of these shipped broken: subtitles never
    rendered anywhere in the app, and the reason was one wrong word."""

    def test_the_vtt_encoder_is_named_webvtt(self):
        # ffmpeg's WebVTT encoder is `webvtt`. Asking for `vtt` fails with
        # "Unknown encoder 'vtt'" and the endpoint 422s, which is exactly what
        # every subtitle request was doing.
        import inspect

        from miru.transcode import subtitles

        src = inspect.getsource(subtitles)
        assert "webvtt" in src, "the encoder must be webvtt, not vtt"

    def test_extraction_asks_ffmpeg_for_webvtt(self, tmp_path, monkeypatch):
        from miru.transcode import subtitles

        seen = {}

        class Done:
            returncode = 0
            stderr = ""

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            # ffmpeg would write the file; stand in for it.
            out = cmd[-1]
            open(out, "w").write("WEBVTT\n\n")
            return Done()

        monkeypatch.setattr(subtitles.subprocess, "run", fake_run)
        src = tmp_path / "in.mkv"
        src.write_bytes(b"\0")
        dest = tmp_path / "out.vtt"
        subtitles.extract_embedded(src, 2, dest)

        cmd = seen["cmd"]
        assert "webvtt" in cmd, f"asked ffmpeg for the wrong encoder: {cmd}"
        assert "vtt" not in [c for c in cmd if c == "vtt"], "must not pass bare 'vtt'"
