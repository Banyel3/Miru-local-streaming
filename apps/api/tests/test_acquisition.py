from miru.acquisition.prowlarr import ProwlarrAria2Provider

NYAA = {  # Nyaa puts the magnet in guid and leaves downloadUrl null
    "guid": "magnet:?xt=urn:btih:abc123&dn=Release",
    "magnetUrl": None,
    "downloadUrl": None,
    "title": "[ToonsHub] BLEACH S01E42 1080p",
    "indexer": "Nyaa.si",
    "size": 1181116006,
    "seeders": 1470,
    "leechers": 12,
    "age": 2,
    "categories": [{"id": 5070, "name": "TV/Anime"}, {"id": 131088}],
    "imdbId": 0,
}

TORZNAB = {  # other indexers do the reverse
    "guid": "https://example/details/1",
    "magnetUrl": None,
    "downloadUrl": "https://example/download/1.torrent",
    "title": "Some.Release.1080p",
    "indexer": "The Pirate Bay",
    "size": 5321218376,
    "seeders": 40,
    "leechers": 3,
    "age": 10,
    "categories": [],
    "imdbId": "tt3728733",
    "tmdbId": 27205,
}

to_result = ProwlarrAria2Provider._to_result


def test_nyaa_magnet_is_taken_from_guid():
    r = to_result(NYAA)
    assert r.magnet == NYAA["guid"]
    assert r.id == NYAA["guid"]          # the magnet IS the identifier
    assert r.grabbable


def test_torrent_url_indexers_use_downloadurl():
    r = to_result(TORZNAB)
    assert r.magnet is None
    assert r.id == TORZNAB["downloadUrl"]
    assert r.grabbable


def test_a_result_with_neither_is_not_grabbable():
    r = to_result({**NYAA, "guid": "https://example/details/9", "downloadUrl": None})
    assert not r.grabbable          # showing it would be a promise the UI can't keep


def test_categories_survive_and_unnamed_ones_are_dropped():
    # Prowlarr emits subcategory entries with an id and no name.
    assert to_result(NYAA).categories == ["TV/Anime"]


def test_ids_are_normalised_for_m2_metadata_matching():
    a, b = to_result(NYAA), to_result(TORZNAB)
    assert a.imdb_id is None          # 0 is not an id
    assert b.imdb_id == "tt3728733" and b.tmdb_id == 27205


def test_missing_numeric_fields_do_not_explode():
    r = to_result({"guid": "magnet:?xt=urn:btih:x", "title": "T", "indexer": "X"})
    assert (r.size_bytes, r.seeders, r.leechers, r.age_days) == (0, 0, 0, 0)
