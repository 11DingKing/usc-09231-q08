import pytest

from beets.library import Item
from beetsplug.deezer import VARIOUS_ARTISTS_ID, DeezerPlugin


@pytest.fixture
def plugin():
    return DeezerPlugin()


class TestSearchQuery:
    def test_track_query_is_free_text(self, plugin):
        query, filters = plugin.get_search_query_with_filters(
            "track", [Item()], "Artist", "Title", False
        )

        assert query == "Title Artist"
        assert filters == {}

    def test_track_query_tolerates_missing_artist(self, plugin):
        query, _ = plugin.get_search_query_with_filters(
            "track", [Item()], "", "Title", False
        )

        assert query == "Title"

    def test_album_query_filters_on_album_and_artist(self, plugin):
        query, filters = plugin.get_search_query_with_filters(
            "album", [Item()], "Artist", "Album", False
        )

        assert query == 'album:"Album" artist:"Artist"'
        assert filters == {}

    def test_album_query_omits_artist_for_various_artists(self, plugin):
        query, _ = plugin.get_search_query_with_filters(
            "album", [Item()], "Various Artists", "Album", True
        )

        assert query == 'album:"Album"'


def make_track(artist_id, position, *, include_artist=True):
    """Build a minimal Deezer album-track payload.

    The album ``/tracks`` endpoint credits each track through its ``artist``
    object (no contributor list); pass ``include_artist=False`` for a track
    that carries neither.
    """
    track = {
        "title": f"Track {position}",
        "id": 1000 + position,
        "link": f"https://www.deezer.com/track/{1000 + position}",
        "duration": 200,
        "track_position": position,
        "disk_number": 1,
    }
    if include_artist and artist_id is not None:
        track["artist"] = {"id": artist_id, "name": f"Artist {artist_id}"}
    return track


def make_album(
    *,
    artist_id=100,
    include_artist=True,
    contributor_ids=None,
    include_contributors=True,
    record_type="album",
):
    """Build a minimal Deezer album payload with selectable artist shapes."""
    album = {
        "title": "Some Album",
        "link": "https://www.deezer.com/album/1",
        "record_type": record_type,
        "label": "Some Label",
        "release_date": "2017-01-01",
        "cover_xl": None,
    }
    if include_artist:
        album["artist"] = {
            "id": artist_id,
            "name": f"Artist {artist_id}",
        }
    if include_contributors and contributor_ids is not None:
        album["contributors"] = [
            {"id": aid, "name": f"Artist {aid}"} for aid in contributor_ids
        ]
    return album


class TestGetTrack:
    def track_data(self, **fields):
        return {
            "id": 1,
            "title": "Title",
            "duration": 100,
            "link": "https://www.deezer.com/track/1",
            **fields,
        }

    def test_uses_contributors_when_artist_is_missing(self, plugin):
        track = plugin._get_track(
            self.track_data(contributors=[{"id": 2, "name": "Artist"}])
        )

        assert track.artist == "Artist"
        assert track.artist_id == "2"

    def test_falls_back_to_artist_without_contributors(self, plugin):
        track = plugin._get_track(
            self.track_data(artist={"id": 2, "name": "Artist"})
        )

        assert track.artist == "Artist"
        assert track.artist_id == "2"

    def test_tolerates_missing_artist_and_contributors(self, plugin):
        track = plugin._get_track(self.track_data())

        assert track.artist is None
        assert track.artist_id is None


class TestAlbumCompilation:
    def album_for_id(self, monkeypatch, plugin, album, track_artist_ids, *,
                     track_without_artist=()):
        """Drive ``album_for_id`` with canned payloads, no network access."""
        tracks = [
            make_track(
                aid,
                position,
                include_artist=position not in track_without_artist,
            )
            for position, aid in enumerate(track_artist_ids, start=1)
        ]

        def fake_fetch_data(url):
            if url.endswith("/tracks"):
                return {"data": tracks}
            return album

        monkeypatch.setattr(plugin, "fetch_data", fake_fetch_data)
        return plugin.album_for_id("1")

    def test_single_main_artist_compilation_is_va(self, plugin, monkeypatch):
        # Deezer attributes the compilation to one "main" artist that only
        # performs on one of the tracks; the old artist-id check missed this.
        album = make_album(
            artist_id=100,
            contributor_ids=[100, 200, 300, 400, 500, 600],
            record_type="compile",
        )

        info = self.album_for_id(
            monkeypatch,
            plugin,
            album,
            [100, 200, 300, 400, 500, 600],
        )

        assert info is not None
        # Compilation fields drive the on-disk layout (albumartist + comp).
        assert info.va is True
        assert info.artist == "Various Artists"
        assert info.artist_credit == "Various Artists"
        assert info.albumtype == "compile"
        # The id keeps pointing at the credited main contributor; only the
        # display artist is replaced by the VA placeholder.
        assert info.artist_id == "100"
        # Track-level artist credits are not flattened to the album artist.
        assert [t.artist for t in info.tracks] == [
            f"Artist {aid}" for aid in [100, 200, 300, 400, 500, 600]
        ]
        assert [t.artist_id for t in info.tracks] == [
            str(aid) for aid in [100, 200, 300, 400, 500, 600]
        ]

    def test_various_artists_entity_is_va(self, plugin, monkeypatch):
        # Multi-artist response already credited to the Deezer VA entity.
        album = make_album(
            artist_id=VARIOUS_ARTISTS_ID,
            contributor_ids=[100, 200, 300],
        )

        info = self.album_for_id(
            monkeypatch, plugin, album, [100, 200, 300, 200]
        )

        assert info is not None
        assert info.va is True
        assert info.artist == "Various Artists"
        assert info.artist_credit == "Various Artists"
        # Per-track credits survive the album-level VA flag.
        assert [t.artist for t in info.tracks] == [
            "Artist 100",
            "Artist 200",
            "Artist 300",
            "Artist 200",
        ]

    def test_plurality_artist_above_threshold_stays_single(
        self, plugin, monkeypatch
    ):
        # Main artist performs on 2/5 tracks (40% >= 25% threshold): this is
        # not a compilation, matching the importer's own plurality rule.
        album = make_album(artist_id=100, contributor_ids=[100])

        info = self.album_for_id(
            monkeypatch, plugin, album, [100, 100, 200, 300, 400]
        )

        assert info is not None
        assert info.va is False
        assert info.artist == "Artist 100"
        assert info.artist_credit == "Artist 100"
        assert info.artist_id == "100"
        assert info.albumtype == "album"

    def test_threshold_boundary_is_not_va(self, plugin, monkeypatch):
        # Exactly 25% of tracks: the strict less-than comparison keeps the
        # release single-artist, as upstream does.
        album = make_album(artist_id=100, contributor_ids=[100])

        info = self.album_for_id(
            monkeypatch, plugin, album, [100, 200, 300, 400]
        )

        assert info is not None
        assert info.va is False
        assert info.artist == "Artist 100"

    def test_single_artist_album_stays_single(self, plugin, monkeypatch):
        album = make_album(artist_id=100, contributor_ids=[100])

        info = self.album_for_id(
            monkeypatch, plugin, album, [100, 100, 100, 100]
        )

        assert info is not None
        assert info.va is False
        assert info.artist == "Artist 100"
        assert info.artist_credit == "Artist 100"
        assert info.artist_id == "100"
        assert all(t.artist == "Artist 100" for t in info.tracks)

    def test_missing_album_artist_falls_back_to_track_plurality(
        self, plugin, monkeypatch
    ):
        # Response with neither an album ``artist`` object nor contributors:
        # the plurality track artist becomes the album artist.
        album = make_album(
            include_artist=False,
            include_contributors=False,
        )

        info = self.album_for_id(
            monkeypatch, plugin, album, [100, 100, 100, 200]
        )

        assert info is not None
        assert info.va is False
        assert info.artist == "Artist 100"
        assert info.artist_credit == "Artist 100"
        assert info.artist_id == "100"

    def test_missing_album_artist_compilation_detected(
        self, plugin, monkeypatch
    ):
        # No album-level artist at all, and no track artist reaches the
        # plurality threshold: still detected as VA.
        album = make_album(
            include_artist=False,
            include_contributors=False,
        )

        info = self.album_for_id(
            monkeypatch, plugin, album, [100, 200, 300, 400, 500]
        )

        assert info is not None
        assert info.va is True
        assert info.artist == "Various Artists"
        assert info.artist_credit == "Various Artists"
        assert info.artist_id == str(VARIOUS_ARTISTS_ID)
        # The track-level credits remain the only per-track artist data.
        assert [t.artist_id for t in info.tracks] == [
            str(aid) for aid in [100, 200, 300, 400, 500]
        ]

    def test_contributors_only_without_artist_object(self, plugin, monkeypatch):
        # Some payloads omit ``artist`` but still carry the contributor list;
        # the main contributor appearing on one of six tracks means VA.
        album = make_album(
            include_artist=False,
            contributor_ids=[100, 200, 300, 400, 500, 600],
        )

        info = self.album_for_id(
            monkeypatch,
            plugin,
            album,
            [100, 200, 300, 400, 500, 600],
        )

        assert info is not None
        assert info.va is True
        assert info.artist == "Various Artists"
        assert info.artist_credit == "Various Artists"
        assert info.artist_id == "100"
        assert [t.artist for t in info.tracks][:2] == [
            "Artist 100",
            "Artist 200",
        ]

    def test_contributors_only_plurality_stays_single(
        self, plugin, monkeypatch
    ):
        # No album ``artist`` object, but the main contributor reaches the
        # plurality threshold: single-artist release. The joined contributors
        # name ``artist`` while ``artist_credit`` names the main one.
        album = make_album(
            include_artist=False,
            contributor_ids=[100, 200],
        )

        info = self.album_for_id(
            monkeypatch, plugin, album, [100, 100, 100, 200]
        )

        assert info is not None
        assert info.va is False
        assert info.artist == "Artist 100, Artist 200"
        assert info.artist_credit == "Artist 100"
        assert info.artist_id == "100"

    def test_no_artists_anywhere(self, plugin, monkeypatch):
        # Degenerate response: no album artist, no contributors, and no track
        # artists. Conversion must not crash or invent an artist.
        album = make_album(
            include_artist=False,
            include_contributors=False,
        )

        info = self.album_for_id(
            monkeypatch,
            plugin,
            album,
            [100, 200],
            track_without_artist=(1, 2),
        )

        assert info is not None
        assert info.va is False
        assert info.artist is None
        assert info.artist_credit is None
        assert info.artist_id is None
        assert all(t.artist is None for t in info.tracks)
        assert all(t.artist_id is None for t in info.tracks)
        # Non-artist fields are still populated.
        assert info.album == "Some Album"
        assert info.year == 2017
        assert len(info.tracks) == 2
