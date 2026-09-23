import pytest

from beets.library import Item
from beetsplug.deezer import DeezerPlugin


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
    """The album-level artist source and the compilation decision must
    cooperate for the different response shapes Deezer returns.
    """

    ALBUM_ID = "302127"

    def track(self, position, artist_id, artist_name, contributors=None):
        return {
            "id": 1000 + position,
            "title": f"Track {position}",
            "duration": 200,
            "link": f"https://www.deezer.com/track/{1000 + position}",
            "track_position": position,
            "disk_number": 1,
            "artist": {"id": artist_id, "name": artist_name},
            "contributors": contributors
            if contributors is not None
            else [{"id": artist_id, "name": artist_name}],
        }

    def album_data(self, artist=None, contributors=None, record_type="album"):
        data = {
            "id": self.ALBUM_ID,
            "title": "Release",
            "release_date": "2020-03-04",
            "record_type": record_type,
            "label": "Label",
            "link": f"https://www.deezer.com/album/{self.ALBUM_ID}",
            "cover_xl": "https://example.com/cover.jpg",
        }
        if artist is not None:
            data["artist"] = artist
        if contributors is not None:
            data["contributors"] = contributors
        return data

    def fetch_album(self, plugin, monkeypatch, album_data, tracks_data):
        def fake_fetch_data(url):
            if url.endswith("/tracks"):
                return {"data": tracks_data}
            return album_data

        monkeypatch.setattr(plugin, "fetch_data", fake_fetch_data)
        return plugin.album_for_id(self.ALBUM_ID)

    def test_regular_single_artist_album(self, plugin, monkeypatch):
        artist = {"id": 10, "name": "The Band"}
        tracks = [self.track(i, 10, "The Band") for i in range(1, 5)]
        album_data = self.album_data(
            artist=artist,
            contributors=[{"id": 10, "name": "The Band"}],
        )

        info = self.fetch_album(plugin, monkeypatch, album_data, tracks)

        assert info.va is False
        assert info.artist == "The Band"
        assert info.artist_credit == "The Band"
        assert info.artist_id == "10"
        assert info.albumtype == "album"
        assert [t.artist for t in info.tracks] == ["The Band"] * 4
        assert [t.artist_id for t in info.tracks] == ["10"] * 4

    def test_various_artists_compilation(self, plugin, monkeypatch):
        va = {"id": 5080, "name": "Various Artists"}
        tracks = [
            self.track(1, 11, "Alpha"),
            self.track(2, 12, "Beta"),
            self.track(3, 13, "Gamma"),
            self.track(4, 14, "Delta"),
        ]
        album_data = self.album_data(
            artist=va,
            contributors=[va],
            record_type="compile",
        )

        info = self.fetch_album(plugin, monkeypatch, album_data, tracks)

        assert info.va is True
        assert info.artist == "Various Artists"
        assert info.artist_credit == "Various Artists"
        assert info.albumtype == "compile"
        # Track-level artist credits must survive the VA rewrite.
        assert [t.artist for t in info.tracks] == [
            "Alpha",
            "Beta",
            "Gamma",
            "Delta",
        ]
        assert [t.artist_id for t in info.tracks] == [
            "11",
            "12",
            "13",
            "14",
        ]

    def test_compilation_credited_to_single_main_artist(
        self, plugin, monkeypatch
    ):
        # Deezer sometimes attributes a compilation to one curating artist
        # instead of the Various Artists entity.
        curator = {"id": 99, "name": "Curator Soundsystem"}
        tracks = [
            self.track(1, 11, "Alpha"),
            self.track(2, 12, "Beta"),
            self.track(3, 13, "Gamma"),
            self.track(4, 14, "Delta"),
        ]
        album_data = self.album_data(
            artist=curator,
            contributors=[{"id": 99, "name": "Curator Soundsystem"}],
            record_type="compile",
        )

        info = self.fetch_album(plugin, monkeypatch, album_data, tracks)

        assert info.va is True
        assert info.artist == "Various Artists"
        assert info.artist_credit == "Various Artists"
        # Track-level artists are not replaced by the curator or VA name.
        assert [t.artist for t in info.tracks] == [
            "Alpha",
            "Beta",
            "Gamma",
            "Delta",
        ]

    def test_single_artist_credit_is_not_misclassified_when_pervasive(
        self, plugin, monkeypatch
    ):
        # The main artist performs on most tracks: not a compilation even
        # though a few guests appear.
        main = {"id": 20, "name": "Headliner"}
        tracks = [
            self.track(1, 20, "Headliner"),
            self.track(2, 20, "Headliner"),
            self.track(3, 20, "Headliner"),
            self.track(
                4,
                21,
                "Guest",
                contributors=[
                    {"id": 21, "name": "Guest"},
                    {"id": 20, "name": "Headliner"},
                ],
            ),
        ]
        album_data = self.album_data(
            artist=main,
            contributors=[{"id": 20, "name": "Headliner"}],
        )

        info = self.fetch_album(plugin, monkeypatch, album_data, tracks)

        assert info.va is False
        assert info.artist == "Headliner"
        assert info.artist_credit == "Headliner"
        assert info.artist_id == "20"
        # The guest track keeps its own (joined) artist credit.
        assert info.tracks[3].artist == "Guest, Headliner"

    def test_missing_album_artist_response(self, plugin, monkeypatch):
        # No "artist" and no "contributors" on the album object; tracks still
        # carry their own artists. Conversion must not crash or claim VA.
        tracks = [
            self.track(1, 11, "Alpha"),
            self.track(2, 12, "Beta"),
        ]
        album_data = self.album_data()

        info = self.fetch_album(plugin, monkeypatch, album_data, tracks)

        assert info.va is False
        assert info.artist is None
        assert info.artist_credit is None
        assert info.artist_id is None
        assert [t.artist for t in info.tracks] == ["Alpha", "Beta"]
        assert [t.artist_id for t in info.tracks] == ["11", "12"]

    def test_missing_album_artist_with_contributors(self, plugin, monkeypatch):
        tracks = [self.track(1, 11, "Alpha")]
        album_data = self.album_data(
            contributors=[{"id": 11, "name": "Alpha"}]
        )

        info = self.fetch_album(plugin, monkeypatch, album_data, tracks)

        assert info.va is False
        assert info.artist == "Alpha"
        assert info.artist_credit == "Alpha"
        assert info.artist_id == "11"
