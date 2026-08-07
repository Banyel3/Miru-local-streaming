from miru.transcode.strategy import (
    DIRECT,
    REMUX,
    TRANSCODE_AUDIO,
    TRANSCODE_FULL,
    Probe,
    resolve_strategy,
)


def test_mp4_h264_aac_is_direct():
    assert resolve_strategy(
        Probe(container="mp4", video_codec="h264", audio_codec="aac", audio_channels=2)
    ) == DIRECT


def test_h264_in_mkv_is_remux():
    assert resolve_strategy(
        Probe(container="mkv", video_codec="h264", audio_codec="aac", audio_channels=2)
    ) == REMUX


def test_surround_ac3_is_audio_transcode_even_in_mp4():
    assert resolve_strategy(
        Probe(container="mp4", video_codec="h264", audio_codec="ac3", audio_channels=6)
    ) == TRANSCODE_AUDIO


def test_51_aac_still_needs_downmix():
    assert resolve_strategy(
        Probe(container="mp4", video_codec="h264", audio_codec="aac", audio_channels=6)
    ) == TRANSCODE_AUDIO


def test_hevc_is_full_transcode_regardless_of_container():
    assert resolve_strategy(
        Probe(container="mp4", video_codec="hevc", audio_codec="aac", audio_channels=2)
    ) == TRANSCODE_FULL


def test_audio_problem_outranks_container_problem():
    # Both wrong: must land on the rung that fixes both, not just the cheaper one.
    assert resolve_strategy(
        Probe(container="mkv", video_codec="h264", audio_codec="dts", audio_channels=6)
    ) == TRANSCODE_AUDIO


def test_subtitles_never_change_the_rung():
    subs = [{"index": 2, "codec": "ass", "language": "eng"}]
    assert resolve_strategy(
        Probe(container="mp4", video_codec="h264", audio_codec="aac",
              audio_channels=2, subtitle_streams=subs)
    ) == DIRECT


def test_silent_video_is_direct_not_an_audio_transcode():
    # Regression: a file with no audio stream was resolving to transcode_audio,
    # queueing a re-encode of a stream that does not exist. Found by scanning a
    # real 10s clip that ships without audio.
    assert resolve_strategy(
        Probe(container="mp4", video_codec="h264", audio_codec=None, audio_channels=None)
    ) == DIRECT


def test_silent_video_in_mkv_still_only_needs_remux():
    assert resolve_strategy(
        Probe(container="mkv", video_codec="h264", audio_codec=None)
    ) == REMUX


def test_unprobed_file_falls_back_to_direct():
    # ffprobe missing or unreadable file: a wrong `direct` costs one failed
    # play, a wrong `transcode_full` costs GPU on every request.
    assert resolve_strategy(Probe()) == DIRECT
