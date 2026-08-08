"""Use case: the file page knows what show it belongs to.

Written before the fix. The library and the catalogue knew nothing about each
other, so clicking a card from the wall lost the poster, the real title and any
sense of what else there is of that show — and the page filled the gap with
"In this folder", which listed every unrelated file in a flat media directory.
"""

import pytest
from sqlalchemy import select

from miru.catalog.models import CatalogRelease, CatalogWork
from tests.conftest import make_file


@pytest.fixture
def series(db_session):
    """One show: two episodes owned, two more available."""
    work = CatalogWork(
        kind="anime", normalised_title="boku no kokoro", display_title="The Dangers in My Heart",
        genres=[], poster_url="https://s4.anilist.co/x.jpg", overview="Two teenagers.",
        score=8.1, release_count=4,
    )
    db_session.add(work)
    db_session.flush()

    owned = make_file(db_session, path="/mnt/storage/media/Boku - 13.mkv", title="Boku - 13")
    other = make_file(db_session, path="/mnt/storage/media/Boku - 12.mkv", title="Boku - 12")
    work.library_file_id = owned.id

    for ep, ih in ((13, "a"), (12, "b"), (11, "c"), (10, "d")):
        db_session.add(CatalogRelease(
            info_hash=ih * 40, indexer="Nyaa.si", guid=f"g{ep}",
            title=f"[Grp] Boku no Kokoro - {ep} [1080p]", kind="anime",
            work_id=work.id, episode=ep, quality="1080p", categories=[],
            magnet=f"magnet:?xt=urn:btih:{ih*40}", seeders=50,
        ))
    db_session.commit()
    return {"work": work, "owned": owned, "other": other}


class TestTheFileKnowsItsSeries:
    def test_the_poster_survives_the_click(self, client, series):
        # Reported as "the images display fine but when clicking something it
        # just disappears": art lives on the catalog work and the library row
        # has no artwork field at all.
        f = client.get(f"/api/files/{series['owned'].id}").json()
        assert f.get("series") is not None
        assert f["series"]["poster_url"]
        assert f["series"]["title"] == "The Dangers in My Heart"

    def test_a_file_with_no_catalogue_entry_says_so_rather_than_guessing(self, client, db_session):
        # Guessing the series from a filename is what produced four cards for
        # one show. A hand-dropped file is shown alone, honestly.
        lone = make_file(db_session, path="/mnt/storage/media/holiday.mp4", title="holiday")
        f = client.get(f"/api/files/{lone.id}").json()
        assert f.get("series") is None
        assert f.get("episodes") == []


class TestTheEpisodeList:
    def test_it_lists_owned_and_available_together(self, client, series):
        eps = client.get(f"/api/files/{series['owned'].id}").json()["episodes"]
        by_num = {e["episode"]: e for e in eps}
        assert set(by_num) == {10, 11, 12, 13}
        # The distinction is a marker on a row, not two different places to look.
        assert by_num[13]["owned"] is True
        assert by_num[11]["owned"] is False

    def test_owned_episodes_carry_the_file_to_play(self, client, series):
        eps = client.get(f"/api/files/{series['owned'].id}").json()["episodes"]
        ep13 = next(e for e in eps if e["episode"] == 13)
        assert ep13["file_id"] == series["owned"].id

    def test_newest_first(self, client, series):
        # Newest is what a weekly watcher wants, and the highest owned episode is
        # where a backlog watcher resumes.
        eps = client.get(f"/api/files/{series['owned'].id}").json()["episodes"]
        assert [e["episode"] for e in eps] == [13, 12, 11, 10]

    def test_a_batch_stays_one_row(self, client, series, db_session):
        # You cannot download episode 8 alone out of a 7-9 torrent.
        db_session.add(CatalogRelease(
            info_hash="e" * 40, indexer="Nyaa.si", guid="gbatch",
            title="[Grp] Boku no Kokoro 07-09 [BD]", kind="anime",
            work_id=series["work"].id, episode=7, episode_end=9,
            quality="1080p", categories=[], magnet="magnet:?xt=urn:btih:" + "e" * 40,
            seeders=30,
        ))
        db_session.commit()
        eps = client.get(f"/api/files/{series['owned'].id}").json()["episodes"]
        batch = [e for e in eps if e.get("episode_end")]
        assert len(batch) == 1 and batch[0]["episode"] == 7 and batch[0]["episode_end"] == 9
        assert 8 not in [e["episode"] for e in eps]
