from pathlib import Path

from miru_worker.ladder import (
    LADDER,
    build_command,
    is_allowed,
    renditions_for,
    session_id,
)


def test_ladder_is_capped_by_the_source():
    # A 720p source must never be upscaled to 1080p: that spends GPU time to
    # make the file bigger and no better.
    assert [r.name for r in renditions_for(720)] == ["720p", "480p"]
    assert [r.name for r in renditions_for(1080)] == ["1080p", "720p", "480p"]
    assert [r.name for r in renditions_for(480)] == ["480p"]


def test_source_smaller_than_every_rung_encodes_at_source():
    got = renditions_for(360)
    assert len(got) == 1 and got[0].height == 360


def test_unknown_source_height_picks_one_safe_rung():
    assert renditions_for(None) == [LADDER[1]]


def test_session_id_is_deterministic_so_two_players_share_one_encode():
    a = session_id("http://host/api/stream/9", 1080, False)
    b = session_id("http://host/api/stream/9", 1080, False)
    assert a == b and len(a) == 16


def test_session_id_separates_different_sources_and_modes():
    base = session_id("http://host/api/stream/9", 1080, False)
    assert session_id("http://host/api/stream/10", 1080, False) != base
    assert session_id("http://host/api/stream/9", 720, False) != base
    assert session_id("http://host/api/stream/9", 1080, True) != base


def test_allowlist_blocks_anything_off_prefix():
    allowed = ["http://localhost:8000/"]
    assert is_allowed("http://localhost:8000/api/stream/9", allowed)
    assert not is_allowed("http://evil.example/x", allowed)
    # The classic near-miss: a host that merely starts with the allowed one.
    assert not is_allowed("http://localhost:8000.evil.example/x", allowed)


def test_command_builds_one_variant_per_rendition():
    cmd = build_command("http://h/s", Path("/tmp/o"), renditions_for(1080), "libx264")
    joined = " ".join(cmd)
    assert "split=3" in joined
    assert "v:0,a:0 v:1,a:1 v:2,a:2" in joined
    assert "-master_pl_name master.m3u8" in joined
    assert "scale=-2:1080" in joined and "scale=-2:480" in joined
    # event, not vod: vod would require the whole encode to finish before play.
    assert "-hls_playlist_type event" in joined


def test_copy_video_produces_a_single_variant_and_no_scaling():
    cmd = build_command("http://h/s", Path("/tmp/o"), [], "libx264", copy_video=True)
    joined = " ".join(cmd)
    assert "-c:v copy" in joined
    assert "split=" not in joined and "scale=" not in joined
    assert "v:0,a:0" in joined


def test_audio_is_identical_across_variants():
    # Differing audio between rungs forces an audio decoder re-init on every
    # quality switch, which stalls video while audio keeps playing.
    cmd = " ".join(build_command("http://h/s", Path("/tmp/o"), renditions_for(1080), "libx264"))
    assert cmd.count("-b:a:0 128k") == 1
    assert cmd.count("-b:a:1 128k") == 1
    assert cmd.count("-b:a:2 128k") == 1
    assert "96k" not in cmd


def test_keyframes_are_forced_by_time_not_by_frame_count():
    # A GOP length in frames only aligns for one frame rate; 25 or 30 fps would
    # put keyframes off the segment boundaries and break switching.
    cmd = " ".join(build_command("http://h/s", Path("/tmp/o"), renditions_for(720), "libx264"))
    assert "expr:gte(t,n_forced*4)" in cmd
    assert "-g " not in cmd


def test_segments_are_fmp4_and_independent():
    cmd = " ".join(build_command("http://h/s", Path("/tmp/o"), renditions_for(720), "libx264"))
    assert "-hls_segment_type fmp4" in cmd
    assert "independent_segments" in cmd
    assert "seg%d.m4s" in cmd


def test_nvenc_and_x264_get_their_own_presets():
    nv = " ".join(build_command("http://h/s", Path("/tmp/o"), renditions_for(720), "h264_nvenc"))
    sw = " ".join(build_command("http://h/s", Path("/tmp/o"), renditions_for(720), "libx264"))
    assert "h264_nvenc" in nv and "-preset p4" in nv
    assert "libx264" in sw and "-preset veryfast" in sw


class TestPixelFormatIsForcedForNvenc:
    """h264_nvenc emits 8-bit 4:2:0 H.264 and cannot take anything else as
    input. Handed a 10-bit or 4:4:4 frame it fails at encoder *open* with

        CreateInputBuffer failed: invalid param (8)
        Could not open encoder before EOF
        -22 (Invalid argument)

    which reads like a bad bitrate or resolution and is neither. Measured on a
    real download: yuv444p10le, i.e. 10-bit AND 4:4:4. Two of the seven files in
    the test library are 10-bit, so this is ordinary anime, not an edge case.
    """

    def _chain(self, height=1080):
        from pathlib import Path

        from miru_worker.ladder import build_command, renditions_for

        cmd = build_command(
            "http://laptop:8000/api/stream/1", Path("/tmp/x"),
            renditions_for(height), "h264_nvenc",
        )
        return " ".join(cmd)

    def test_every_encoded_branch_converts_to_nv12(self):
        chain = self._chain()
        assert "format=nv12" in chain, (
            "a 10-bit or 4:4:4 source reaches h264_nvenc unconverted and the "
            "encoder refuses to open"
        )

    def test_the_conversion_comes_after_the_scale(self):
        # Scaling in the source format and converting once at the end is the
        # cheap order; converting first would scale 8-bit and lose precision
        # for nothing.
        chain = self._chain()
        assert "scale=-2:" in chain
        for part in chain.split(";"):
            if "scale=-2:" in part:
                assert part.index("scale=-2:") < part.index("format=nv12")

    def test_copying_the_video_does_not_convert(self):
        # A remux never decodes, so there is no frame to convert and adding a
        # filter would force a needless decode/encode cycle.
        from pathlib import Path

        from miru_worker.ladder import build_command, renditions_for

        cmd = " ".join(build_command(
            "http://laptop:8000/api/stream/1", Path("/tmp/x"),
            renditions_for(1080), "h264_nvenc", copy_video=True,
        ))
        assert "format=nv12" not in cmd
