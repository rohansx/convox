from convox.audio.clock import FrameClock
from convox.clock import FakeClock


def test_stamps_are_monotonic_20ms_steps():
    fc = FrameClock()
    assert [fc.stamp() for _ in range(4)] == [0, 20, 40, 60]


def test_observe_steady_stream_has_no_gaps():
    clock, fc = FakeClock(), FrameClock()
    stamps = []
    for _ in range(50):
        assert fc.observe(clock.now_ms()) is None
        stamps.append(fc.last_ms)
        clock.advance(20)
    assert stamps == [i * 20 for i in range(50)]


def test_small_jitter_below_threshold_is_not_a_gap():
    fc = FrameClock()
    for i, arrival in enumerate([0, 20, 40, 230, 80]):  # frame 3 is 170 ms late
        assert fc.observe(arrival) is None
        assert fc.last_ms == i * 20
    assert fc.jitter_ms == 0


def test_stall_over_threshold_reports_gap_and_advances_clock():
    clock, fc = FakeClock(start_ms=1000), FrameClock()  # anchor is the first arrival
    for _ in range(3):
        fc.observe(clock.now_ms())
        clock.advance(20)
    clock.advance(2000)  # 2 s provider drop
    gap = fc.observe(clock.now_ms())
    assert gap == 2000
    assert fc.last_ms == 60 + 2000  # timestamps stay true
    clock.advance(20)
    assert fc.observe(clock.now_ms()) is None
    assert fc.last_ms == 2080


def test_threshold_is_configurable():
    fc = FrameClock(gap_threshold_ms=50)
    fc.observe(0)
    assert fc.observe(100) == 80
    assert fc.last_ms == 100


def test_early_burst_after_late_frame_is_not_a_gap():
    fc = FrameClock()
    fc.observe(0)
    for arrival in (5, 6, 7):  # provider delivers frames faster than real time
        assert fc.observe(arrival) is None
    assert fc.last_ms == 60
    assert fc.jitter_ms == 7 - 60
