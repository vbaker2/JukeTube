import pytest
from types import SimpleNamespace

from jukebox.models import Collection, Track
from jukebox.sources.base import SourceError
from jukebox.sources.ytmusic import YouTubeMusicSource, _best_thumb, _parse_length

SONG = {
    "resultType": "song", "title": "Get Lucky", "videoId": "4D7u5KF7SP8",
    "album": {"name": "Random Access Memories", "id": "MPREb_x"},
    "artists": [{"name": "Daft Punk", "id": "UC1"}, {"name": "Pharrell Williams", "id": "UC2"}],
    "duration": "6:10", "duration_seconds": 370, "isExplicit": False,
    "thumbnails": [{"url": "https://yt3.googleusercontent.com/abc=w60-h60-l90-rj"}],
}


class FakeYT:
    def __init__(self, broken=()):
        self.broken = set(broken)

    def _check(self, name):
        if name in self.broken:
            raise KeyError("navigationEndpoint")

    def search(self, query, filter=None, limit=20):
        self._check(f"search:{filter}")
        if filter == "songs":
            return [SONG, dict(SONG, videoId="x2", title="Explicit", isExplicit=True),
                    dict(SONG, videoId=None), dict(SONG, videoId="x3", isAvailable=False)]
        if filter == "artists":
            return [{"resultType": "artist", "artist": "Daft Punk", "browseId": "UCdp", "thumbnails": []}]
        if filter == "albums":
            return [{"resultType": "album", "title": "Discovery", "browseId": "MPREb_disc",
                     "type": "Album", "artists": [{"name": "Daft Punk"}], "year": "2001"}]
        return [{"resultType": "playlist", "title": "French Touch", "browseId": "VLRDCLAKxyz",
                 "itemCount": 50, "author": "YouTube Music"}]

    def get_home(self, limit=6):
        self._check("home")
        return [{"title": "Hits", "contents": [SONG, {"title": "Mix", "playlistId": "RDmix"}, {"junk": 1}]},
                {"title": "Empty", "contents": []}]

    def get_playlist(self, pid, limit=200):
        self._check("playlist")
        assert not pid.startswith("VL")
        return {"tracks": [{"videoId": "p1", "title": "One", "artists": [{"name": "A"}], "length": "3:05",
                            "thumbnails": None}]}

    def get_album(self, browse_id):
        return {"title": "Discovery", "thumbnails": [{"url": "https://x/art=w120-h120"}],
                "tracks": [{"videoId": "a1", "title": "One More Time", "artists": [{"name": "Daft Punk"}],
                            "duration_seconds": 320}]}

    def get_artist(self, cid):
        return {"songs": {"results": [SONG]},
                "albums": {"results": [{"title": "Homework", "browseId": "MPREb_hw"}]}}

    def get_charts(self, country):
        self._check("charts")
        self.chart_requests = getattr(self, "chart_requests", []) + [country]
        return {"videos": [{"title": f"Trending 20 {country}", "playlistId": "PLtrend"},
                           {"title": f"Top 100 Music Videos {country}", "playlistId": "PLtop"}],
                "artists": [{"title": "Artist X", "browseId": "UCx"}]}

    def get_watch_playlist(self, videoId, limit=25):
        return {"tracks": [dict(SONG, videoId=videoId), dict(SONG, videoId="r2", title="Next")]}


def src(**kw):
    broken = kw.pop("broken", ())
    return YouTubeMusicSource(client=FakeYT(broken), **kw)


def test_brand_account_id_is_passed_to_ytmusic(monkeypatch):
    calls = {}

    def fake_ytmusic(auth, **kwargs):
        calls.update(auth=auth, **kwargs)
        return FakeYT()

    monkeypatch.setitem(__import__("sys").modules, "ytmusicapi",
                        SimpleNamespace(YTMusic=fake_ytmusic))
    YouTubeMusicSource(auth_file="account.json", user="123456789")
    assert calls == {"auth": "account.json", "user": "123456789",
                     "language": "en", "location": "GB"}


def test_parse_helpers():
    assert _parse_length("6:10") == 370
    assert _parse_length("1:02:03") == 3723
    assert _parse_length("bad") is None
    assert _best_thumb([{"url": "https://a/b=w60-h60-l90"}]) == "https://a/b=w300-h300-l90"
    assert _best_thumb([{"url": "https://a/b=s192"}]) == "https://a/b=s300"
    assert _best_thumb(None) is None


def test_search_mixes_tracks_and_collections_and_filters_bad_rows():
    items = src().search("daft punk")
    tracks = [i for i in items if isinstance(i, Track)]
    assert [t.id for t in tracks] == ["4D7u5KF7SP8", "x2"]
    t = tracks[0]
    assert t.artist == "Daft Punk, Pharrell Williams"
    assert t.album == "Random Access Memories" and t.duration == 370
    kinds = [i.kind for i in items if isinstance(i, Collection)]
    assert kinds == ["artist", "album", "playlist"]
    playlist = items[-1]
    assert playlist.ref == "RDCLAKxyz"  # "VL" prefix stripped


def test_explicit_filter():
    items = src(allow_explicit=False).search("x")
    assert all(not i.explicit for i in items if isinstance(i, Track))


def test_search_survives_one_broken_endpoint():
    items = src(broken={"search:albums"}).search("x")
    assert not any(isinstance(i, Collection) and i.kind == "album" for i in items)
    assert any(isinstance(i, Track) for i in items)


def test_search_all_broken_raises():
    broken = {f"search:{f}" for f in ("songs", "artists", "albums", "featured_playlists")}
    with pytest.raises(SourceError):
        src(broken=broken).search("x")


def test_home_has_menu_and_shelves():
    shelves = src().home()
    assert shelves[0].title == "Browse"
    menu = shelves[0].items
    assert [(c.kind, c.title, c.ref) for c in menu] == [
        ("chart", "UK Charts", "GB"), ("chart", "Russian Charts", "RU"),
        ("chart", "Estonian Charts", "EE"), ("menu", "Genres", "genres"),
        ("menu", "Artists", "artists")]
    assert shelves[1].title == "Hits" and len(shelves[1].items) == 2
    assert len(shelves) == 2  # empty shelf dropped


def test_home_survives_broken_feed():
    shelves = src(broken={"home"}).home()
    assert len(shelves) == 1


def test_expand_variants():
    s = src()
    pl = s.expand(Collection("playlist", "P", "RDmix", art_url="https://fallback"))
    assert pl[0].duration == 185 and pl[0].art_url == "https://fallback"
    album = s.expand(Collection("album", "Discovery", "MPREb_disc"))
    assert album[0].album == "Discovery" and album[0].art_url.endswith("w300-h300")
    artist = s.expand(Collection("artist", "Daft Punk", "UCdp"))
    assert isinstance(artist[0], Track) and artist[-1].kind == "album"
    genres = s.expand(Collection("menu", "Genres", "genres"))
    assert genres[0].kind == "genre"
    artists = s.expand(Collection("menu", "Artists", "artists"))
    assert [a.title for a in artists] == ["Back Street Boys", "The Prodigy", "Knife Party",
                                         "Swedish House Mafia"]
    assert artists[0].kind == "artist"
    assert s.expand(artists[0])[0].title == "Get Lucky"


def test_expand_broken_playlist_raises_friendly_error():
    with pytest.raises(SourceError):
        src(broken={"playlist"}).expand(Collection("playlist", "P", "RDmix"))


def test_radio_excludes_seed():
    s = src()
    seed = s.search("x")[0]
    assert [t.id for t in s.radio(seed)] == ["r2"]


def test_playable_url():
    assert src().playable_url(Track("abc", "t")) == "https://music.youtube.com/watch?v=abc"


def test_popular_dance_uses_uk_radio_searches_and_deduplicates(monkeypatch):
    class DanceYT:
        def __init__(self):
            self.queries = []

        def search(self, query, filter=None, limit=20):
            self.queries.append((query, filter, limit))
            return [dict(SONG, videoId=f"{query}-track"), SONG]

    import jukebox.sources.ytmusic as ytmusic
    monkeypatch.setattr(ytmusic.random, "shuffle", lambda tracks: None)
    client = DanceYT()
    tracks = YouTubeMusicSource(client=client).popular_dance(limit=10)
    assert [query for query, _, _ in client.queries] == [
        "Kiss Dance", "UK dance hits", "UK club anthems"]
    assert all(filter == "songs" and limit == 25 for _, filter, limit in client.queries)
    assert len(tracks) == 4
    assert len({track.id for track in tracks}) == 4


def test_chart_tab_opens_top_100_songs_then_other_lists():
    s = src()
    items = s.expand(Collection("chart", "Estonian Charts", "EE"))
    assert s.yt.chart_requests == ["EE"]
    assert isinstance(items[0], Track)  # Top 100 songs come first
    rest = [(i.kind, i.title) for i in items if isinstance(i, Collection)]
    assert rest == [("playlist", "Trending 20 EE"), ("artist", "Artist X")]


def test_custom_chart_list():
    shelves = src(charts=[{"name": "US", "country": "us"}]).home()
    assert [c.ref for c in shelves[0].items if c.kind == "chart"] == ["US"]


def test_house_music_comes_from_first_chart():
    s = src()
    tracks = s.house_music()
    assert s.yt.chart_requests == ["GB"]
    assert tracks and all(isinstance(t, Track) for t in tracks)


def test_house_music_falls_back_when_charts_broken():
    tracks = src(broken={"charts"}).house_music()
    assert [t.id for t in tracks] == ["p1"]  # first home playlist


class GenreYT(FakeYT):
    def search(self, query, filter=None, limit=20):
        self.genre_queries = getattr(self, "genre_queries", []) + [(query, filter)]
        if filter == "featured_playlists":
            return [{"title": "Britpop Anthems", "playlistId": "PL1"},
                    {"title": "'90s Punjabi Party", "playlistId": "PL2"},  # no keyword
                    {"title": "Britpop R&B Chill", "playlistId": "PL3"}]  # excluded style
        return [{"title": "100 Best Britpop Songs", "playlistId": "PL4"},
                {"title": "Britpop Anthems", "playlistId": "PL1"}]  # duplicate


def test_genre_keeps_only_matching_european_playlists():
    s = YouTubeMusicSource(client=GenreYT())
    items = s.expand(Collection("genre", "Britpop", "Britpop"))
    assert [i.title for i in items] == ["Britpop Anthems", "100 Best Britpop Songs"]
    assert all(i.genre == "Britpop" for i in items)
    assert s.expand(items[0])[0].genre == "Britpop"
    assert s.yt.genre_queries == [("britpop", "featured_playlists"), ("britpop", "community_playlists")]


def test_genre_menu_is_european_with_descriptions():
    menu = src().expand(Collection("menu", "Genres", "genres"))
    names = [c.title for c in menu]
    assert "Britpop" in names and "Eesti muusika" in names
    assert {"Big Room House", "Dubstep", "Handbag House", "Boy Band"} <= set(names)
    assert not {"Northern Soul", "Italo Disco", "Folk & Celtic", "French Chanson",
                "Schlager", "Русский рок", "Русская поп-музыка"} & set(names)
    assert len(names) == 25
    assert not any(x in " ".join(names) for x in ("Hip-Hop", "R&B", "Country"))
    assert menu[names.index("Britpop")].subtitle.startswith("Oasis")


def test_custom_genres_accept_plain_names():
    s = YouTubeMusicSource(client=GenreYT(), genres=["Britpop"], genre_exclude=[])
    assert [c.title for c in s.expand(Collection("menu", "G", "genres"))] == ["Britpop"]
    titles = [i.title for i in s.expand(Collection("genre", "Britpop", "Britpop"))]
    assert "Britpop R&B Chill" in titles  # exclusions switched off


def test_genre_with_no_matches_errors_politely():
    class Empty(FakeYT):
        def search(self, *a, **k):
            return []

    with pytest.raises(SourceError):
        YouTubeMusicSource(client=Empty()).expand(Collection("genre", "Britpop", "Britpop"))
