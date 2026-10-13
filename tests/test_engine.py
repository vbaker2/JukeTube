from concurrent.futures import Future

from jukebox.engine import EngineConfig, Jukebox
from jukebox.player import SimulatedPlayer
from jukebox.sources.demo import DemoSource, _track


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def sync_submit(fn, *args):
    f = Future()
    try:
        f.set_result(fn(*args))
    except Exception as exc:  # pragma: no cover
        f.set_exception(exc)
    return f


def make(**cfg):
    clock = Clock()
    player = SimulatedPlayer(default_length=100, clock=clock)
    eng = Jukebox(DemoSource(), player, EngineConfig(**cfg), submit=sync_submit, clock=clock)
    return eng, player, clock


def test_free_play_request_starts_playing():
    eng, player, _ = make(autoplay=False)
    ok, _ = eng.request(_track(1))
    assert ok
    eng.tick()
    assert eng.now_playing.track == _track(1)
    assert player.url == "demo://demo1"


def test_credits_required_when_not_free_play():
    eng, _, _ = make(free_play=False, autoplay=False)
    ok, msg = eng.request(_track(1))
    assert not ok and "coin" in msg.lower()
    eng.insert_coin()
    assert eng.credits == 1
    assert eng.request(_track(1))[0]
    assert eng.credits == 0
    assert not eng.request(_track(2))[0]


def test_songs_per_credit_and_cap():
    eng, _, _ = make(free_play=False, songs_per_credit=3, max_credits=5)
    eng.insert_coin()
    eng.insert_coin()
    assert eng.credits == 5


def test_duplicate_requests_rejected():
    eng, _, _ = make(autoplay=False)
    assert eng.request(_track(1))[0]
    eng.tick()  # now playing
    ok, msg = eng.request(_track(1))
    assert not ok and "already" in msg


def test_queue_advances_when_track_ends():
    eng, _, clock = make(autoplay=False)
    eng.request(_track(1))
    eng.request(_track(2))
    eng.tick()
    assert eng.now_playing.track == _track(1)
    clock.t += 101
    eng.tick()
    assert eng.now_playing.track == _track(2)
    assert eng.history[0] == _track(1)


def test_requests_jump_ahead_of_autoplay_and_interrupt_it():
    eng, _, clock = make(autoplay_delay=0)
    eng.tick()  # fetch radio
    eng.tick()  # start autoplay
    assert eng.now_playing and not eng.now_playing.requested
    eng.request(_track(50))
    assert eng.now_playing.track == _track(50)
    assert eng.now_playing.requested


def test_autoplay_waits_when_interrupt_disabled():
    eng, _, _ = make(autoplay_delay=0, interrupt_autoplay=False)
    eng.tick()
    eng.tick()
    filler = eng.now_playing
    eng.request(_track(50))
    assert eng.now_playing is filler
    assert [e.track for e in eng.up_next] == [_track(50)]


def test_autoplay_respects_delay():
    eng, _, clock = make(autoplay_delay=5)
    eng.tick()
    assert eng.now_playing is None
    clock.t += 6
    eng.tick()
    eng.tick()
    assert eng.now_playing is not None


def test_request_order_is_fifo():
    eng, _, _ = make(autoplay=False)
    for i in range(1, 4):
        eng.request(_track(i))
    eng.tick()
    assert [e.track for e in eng.up_next] == [_track(2), _track(3)]


def test_max_queue():
    eng, _, _ = make(autoplay=False, max_queue=2)
    assert eng.request(_track(1))[0]
    assert eng.request(_track(2))[0]
    assert not eng.request(_track(3))[0]


def test_request_many_spends_available_credits_only():
    eng, _, _ = make(free_play=False, autoplay=False)
    eng.insert_coin()
    eng.insert_coin()
    added, _ = eng.request_many([_track(i) for i in range(5)])
    assert added == 2 and eng.credits == 0


def test_skip_and_disabled_skip():
    eng, _, _ = make(autoplay=False)
    eng.request(_track(1))
    eng.request(_track(2))
    eng.tick()
    assert eng.skip()
    assert eng.now_playing.track == _track(2)
    eng.cfg.allow_skip = False
    assert not eng.skip()


def test_errors_back_off():
    eng, player, clock = make(autoplay=False)

    def fail():
        player._finished = "error"
        player.url = None
    for i in range(8):
        eng.request(_track(i))
    eng.tick()
    for _ in range(6):
        fail()
        eng.tick()
    # After repeated failures the engine waits instead of burning the queue.
    remaining = len(eng.up_next)
    fail()
    eng.tick()
    eng.tick()
    assert len(eng.up_next) == remaining
    assert eng.now_playing is None


def test_autoplay_uses_house_music_not_radio_by_default():
    eng, _, clock = make(autoplay_delay=0)
    calls = []
    eng.source.radio = lambda t, limit=25: calls.append(t) or [_track(99)]
    eng.request(_track(1))
    eng.tick()
    clock.t += 101  # song ends
    eng.tick()
    eng.tick()
    assert not calls
    assert eng.now_playing and not eng.now_playing.requested


def test_autoplay_radio_option_follows_last_song():
    eng, _, clock = make(autoplay_delay=0, autoplay_radio=True)
    eng.source.radio = lambda t, limit=25: [_track(99)]
    eng.request(_track(1))
    eng.tick()
    clock.t += 101
    eng.tick()
    eng.tick()
    assert eng.now_playing.track == _track(99)
