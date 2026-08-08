"""Use case: the metadata provider answers, but answers about a different show.

`lookup()` shortens a title progressively until something matches, because
release names carry tails no provider has heard of. It checks that a *shortened*
search found the right thing. It never checked the full-length one — and the
full-length one is where the worst answers came from, because a badly parsed
title is full-length by definition.

Live consequence: a Frieren release parsed to `CHS`, TMDB answered
`CHS: Dash for the Cash`, and it was accepted without a question because the
search was full-length. `Les Ch'tis` is the same failure a second time.
"""

import pytest

from miru.catalog import enrich


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    monkeypatch.setattr(enrich, "_MIN_INTERVAL", 0.0)


def _answer(title, *names):
    return {"provider": "tmdb", "provider_id": "1", "display_title": title,
            "names": list(names) or [title]}


class TestAFullLengthMatchIsCheckedToo:
    def test_an_answer_that_names_a_different_show_is_refused(self, monkeypatch):
        # The reported case, exactly: query "CHS", answer "CHS: Dash for the
        # Cash". Nothing in the answer's names is what was asked about.
        monkeypatch.setattr(enrich, "_anilist", lambda t: None)
        monkeypatch.setattr(
            enrich, "_tmdb",
            lambda t, y, k: _answer("CHS: Dash for the Cash", "CHS: Dash for the Cash"),
        )
        assert enrich.lookup("anime", "CHS", None) is None

    def test_an_answer_that_does_name_the_show_is_accepted(self, monkeypatch):
        # The guard must not cost the normal case. AniList answers "Sousou no
        # Frieren" to "Sousou no Frieren".
        monkeypatch.setattr(
            enrich, "_anilist",
            lambda t: _answer("Frieren: Beyond Journey's End",
                              "Sousou no Frieren", "Frieren: Beyond Journey's End"),
        )
        got = enrich.lookup("anime", "Sousou no Frieren", None)
        assert got["display_title"] == "Frieren: Beyond Journey's End"

    def test_the_provider_may_answer_under_a_name_we_did_not_ask_about(self, monkeypatch):
        # THE case the whole design exists for: ask "Youjo Senki", get back a
        # record whose display title is "Saga of Tanya the Evil". Accepted
        # because the record ALSO carries the name that was asked about.
        monkeypatch.setattr(
            enrich, "_anilist",
            lambda t: _answer("Saga of Tanya the Evil", "Youjo Senki",
                              "Saga of Tanya the Evil", "幼女戦記"),
        )
        got = enrich.lookup("anime", "Youjo Senki", None)
        assert got["display_title"] == "Saga of Tanya the Evil"


class TestATitleThatCannotIdentifyAnything:
    @pytest.mark.parametrize("junk", ["CHS", "GB", "v2", "01", "", "  "])
    def test_it_is_not_sent_to_a_provider_at_all(self, monkeypatch, junk):
        # A single short token names no show. Asking is a lottery whose prize
        # is a wrong card, and a wrong card offers the wrong download.
        asked = []
        monkeypatch.setattr(enrich, "_anilist", lambda t: asked.append(t))
        monkeypatch.setattr(enrich, "_tvmaze", lambda t: asked.append(t))
        monkeypatch.setattr(enrich, "_tmdb", lambda t, y, k: asked.append(t))
        assert enrich.lookup("anime", junk, None) is None
        assert asked == [], f"asked a provider about {junk!r}"

    def test_a_short_but_real_title_is_still_asked_about(self, monkeypatch):
        # Real shows have short names. "Naruto", "BLEACH", "Monster", "Akira".
        # The rule has to be about being unidentifiable, not about being short.
        monkeypatch.setattr(enrich, "_anilist", lambda t: _answer("Akira", "Akira"))
        assert enrich.lookup("anime", "Akira", None)["display_title"] == "Akira"

    def test_a_two_word_title_is_asked_about(self, monkeypatch):
        monkeypatch.setattr(enrich, "_anilist", lambda t: _answer("Your Name", "Your Name"))
        assert enrich.lookup("anime", "Your Name", None)["display_title"] == "Your Name"
