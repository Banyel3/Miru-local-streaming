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


def test_nvenc_and_x264_get_their_own_presets():
    nv = " ".join(build_command("http://h/s", Path("/tmp/o"), renditions_for(720), "h264_nvenc"))
    sw = " ".join(build_command("http://h/s", Path("/tmp/o"), renditions_for(720), "libx264"))
    assert "h264_nvenc" in nv and "-preset p4" in nv
    assert "libx264" in sw and "-preset veryfast" in sw
