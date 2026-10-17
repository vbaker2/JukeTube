import json
from collections import deque

from jukebox.models import Track
from jukebox.updater import UpdateAgent


def test_restore_queue_after_update(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    queued = [{
        "track": {
            "id": "video-1", "title": "Dance song", "artist": "Artist",
            "album": "Album", "duration": 204, "art_url": "https://example.test/art",
            "source": "ytmusic", "explicit": False, "genre": "Dance",
        },
        "requested": True,
    }]
    (state / "update-queue.json").write_text(json.dumps(queued))
    engine = type("Engine", (), {"queue": deque()})()

    UpdateAgent(tmp_path, state).restore_queue(engine)

    assert len(engine.queue) == 1
    assert engine.queue[0].track == Track(**queued[0]["track"])
    assert engine.queue[0].requested
    assert not (state / "update-queue.json").exists()
