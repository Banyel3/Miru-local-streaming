"""Use case: an anime library.

The shapes here are what fansub releases actually look like — MKV containers,
Hi10p video, ASS subtitles beside the file, Japanese titles — because those are
the cases Miru gets wrong in ways that only show up on a real library.
"""

from tests.conftest import make_file
from miru.transcode.strategy import Probe, resolve_strategy
from miru.transcode.subtitles import find_sidecars, format_for


class TestAnimeStrategies:
    def test_the_standard_fansub_shape_needs_only_a_remux(self):
        # MKV + H.264 8-bit + AAC stereo is the majority of an anime library.
        # It must not reach the GPU, or the PC becomes a dependency for almost
        # everything.
        assert resolve_strategy(
            Probe(container="mkv", video_codec="h264", audio_codec="aac", audio_channels=2)
        ) == "remux"

    def test_hevc_release_needs_the_gpu(self):
        assert resolve_strategy(
            Probe(container="mkv", video_codec="hevc", audio_codec="aac", audio_channels=2)
        ) == "transcode_full"

    def test_a_silent_clip_still_plays_directly(self):
        # Openings, title cards and no-audio rips: a missing audio stream is not
        # a broken audio stream.
        assert resolve_strategy(
            Probe(container="mp4", video_codec="h264", audio_codec=None)
        ) == "direct"

    def test_japanese_surround_track_transcodes_audio_only(self):
        assert resolve_strategy(
            Probe(container="mp4", video_codec="h264", audio_codec="ac3", audio_channels=6)
        ) == "transcode_audio"

    def test_subtitles_never_change_the_rung(self):
        subs = [{"codec": "ass", "language": "jpn"}, {"codec": "ass", "language": "eng"}]
        assert resolve_strategy(
            Probe(container="mp4", video_codec="h264", audio_codec="aac",
                  audio_channels=2, subtitle_streams=subs)
        ) == "direct"


class TestAnimeSubtitles:
    def test_sidecar_ass_beside_the_episode_is_found(self, tmp_path):
        video = tmp_path / "[Group] Show - 01 [1080p].mkv"
        video.write_bytes(b"v")
        (tmp_path / "[Group] Show - 01 [1080p].ass").write_text("[Script Info]")

        found = find_sidecars(video)
        assert len(found) == 1
        assert found[0]["codec"] == "ass" and found[0]["source"] == "external"

    def test_multiple_language_sidecars_are_all_found_and_labelled(self, tmp_path):
        video = tmp_path / "Show - 02.mkv"
        video.write_bytes(b"v")
        for tag in ("eng", "jpn", "spa"):
            (tmp_path / f"Show - 02.{tag}.ass").write_text("x")

        langs = sorted(s["language"] for s in find_sidecars(video))
        assert langs == ["eng", "jpn", "spa"]

    def test_a_neighbours_subtitles_are_not_claimed(self, tmp_path):
        # Episode 1 must not pick up episode 2's subtitle file.
        (tmp_path / "Show - 01.mkv").write_bytes(b"v")
        (tmp_path / "Show - 02.ass").write_text("x")
        assert find_sidecars(tmp_path / "Show - 01.mkv") == []

    def test_ass_keeps_styling_and_srt_becomes_vtt(self):
        # ASS carries typesetting and positioning that VTT cannot express, so it
        # is served as-is and rendered client-side.
        assert format_for("ass") == "ass"
        assert format_for("ssa") == "ass"
        assert format_for("subrip") == "vtt"
        assert format_for("mov_text") == "vtt"


class TestAnimeBrowsing:
    def test_episodes_of_a_series_are_listed_together(self, client, db_session):
        for n in (1, 2, 3):
            make_file(
                db_session,
                path=f"/mnt/storage/media/Bleach/Season 1/Bleach - S01E{n:02d}.mkv",
                title=f"Bleach - S01E{n:02d}",
                container="mkv",
                playback_strategy="remux",
            )
        make_file(db_session, path="/mnt/storage/media/Other/Other - S01E01.mkv",
                  title="Other - S01E01")

        body = client.get("/api/library?q=Bleach").json()
        assert len(body) == 3
        assert all("Bleach" in f["title"] for f in body)

    def test_a_japanese_title_survives_search_and_serialisation(self, client, db_session):
        make_file(db_session, path="/mnt/storage/media/死神/死神 - 01.mkv", title="死神 - 01")
        body = client.get("/api/library?q=死神").json()
        assert len(body) == 1 and body[0]["title"] == "死神 - 01"

    def test_subtitle_tracks_are_published_for_the_player(self, client, db_session):
        f = make_file(
            db_session,
            subtitle_streams=[
                {"index": 2, "codec": "ass", "language": "jpn", "source": "embedded"},
                {"index": None, "codec": "ass", "language": "eng", "source": "external",
                 "path": "/mnt/storage/media/x.eng.ass"},
            ],
        )
        body = client.get(f"/api/files/{f.id}").json()
        assert len(body["subtitle_streams"]) == 2
        assert {s["source"] for s in body["subtitle_streams"]} == {"embedded", "external"}
