import time
from pathlib import Path

from miru.library.incoming import is_settled, promote


def _aged(path: Path, seconds: float) -> None:
    """Backdate a file so it looks like nothing has written to it recently."""
    old = time.time() - seconds
    import os

    os.utime(path, (old, old))


def test_a_file_still_being_written_is_not_settled(tmp_path):
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x")
    assert not is_settled(f, settle_seconds=120)


def test_a_quiet_file_is_settled(tmp_path):
    f = tmp_path / "movie.mkv"
    f.write_bytes(b"x")
    _aged(f, 300)
    assert is_settled(f, settle_seconds=120)


def test_partial_extensions_are_never_settled_however_quiet(tmp_path):
    # A stalled torrent looks still. The .part suffix is the one hard signal
    # that it is unfinished, so it outranks the stillness heuristic.
    for name in ("movie.mkv.part", "movie.mkv.!qB", "movie.mkv.aria2"):
        f = tmp_path / name
        f.write_bytes(b"x")
        _aged(f, 9999)
        assert not is_settled(f, settle_seconds=120), name


def test_a_folder_is_judged_by_its_newest_file(tmp_path):
    d = tmp_path / "Some.Release"
    d.mkdir()
    (d / "a.mkv").write_bytes(b"x")
    (d / "b.nfo").write_bytes(b"x")
    _aged(d / "a.mkv", 300)
    _aged(d / "b.nfo", 300)
    assert is_settled(d, settle_seconds=120)
    # One fresh write anywhere inside means the whole entry is still in flight.
    (d / "c.mkv").write_bytes(b"x")
    assert not is_settled(d, settle_seconds=120)


def test_promote_moves_only_settled_entries(tmp_path):
    inc, lib = tmp_path / "incoming", tmp_path / "media"
    inc.mkdir(); lib.mkdir()

    done = inc / "Finished.mkv"; done.write_bytes(b"x"); _aged(done, 300)
    busy = inc / "Downloading.mkv"; busy.write_bytes(b"x")
    part = inc / "Stalled.mkv.part"; part.write_bytes(b"x"); _aged(part, 300)

    assert promote(inc, lib, settle_seconds=120) == {"promoted": 1, "waiting": 2}
    assert (lib / "Finished.mkv").exists()
    assert not done.exists()
    assert busy.exists() and part.exists()


def test_promote_never_overwrites_something_already_in_the_library(tmp_path):
    inc, lib = tmp_path / "incoming", tmp_path / "media"
    inc.mkdir(); lib.mkdir()
    (lib / "Movie.mkv").write_bytes(b"original")
    dup = inc / "Movie.mkv"; dup.write_bytes(b"new"); _aged(dup, 300)

    assert promote(inc, lib, settle_seconds=120) == {"promoted": 0, "waiting": 1}
    assert (lib / "Movie.mkv").read_bytes() == b"original"


def test_dotfiles_are_ignored(tmp_path):
    inc, lib = tmp_path / "incoming", tmp_path / "media"
    inc.mkdir(); lib.mkdir()
    hidden = inc / ".from-pc"; hidden.write_bytes(b"x"); _aged(hidden, 300)
    assert promote(inc, lib, settle_seconds=120) == {"promoted": 0, "waiting": 0}
    assert hidden.exists()
