"""Session-driven changes to the FloorController: the line-check event and bridge mode."""

from tests.floor_helpers import LINE_CHECK_MARKER, Rig


async def test_line_check_publishes_event_with_both_sides():
    rig = Rig()
    await rig.advance(10000)
    (ev,) = rig.bus.kinds("line_check")
    assert sorted(ev.payload["sides"]) == ["a", "b"]
    assert ev.call_id == "c_test" and ev.ts_ms == 10000


async def test_no_line_check_event_without_a_prompt_source():
    rig = Rig()
    rig.fc = rig.make(line_check=None)
    await rig.advance(11000)
    assert rig.bus.kinds("line_check") == []


async def test_floor_can_start_in_bypass_for_bridge_mode():
    rig = Rig()
    rig.fc = rig.make(bypass=True)
    fc = rig.fc
    assert fc.bypass
    await fc.forward("a", b"\x07" * 640)
    assert [e.data for e in rig.legs["b"].entries("send")] == [b"\x07" * 640]
    # no bypass event and no escape: bridge mode is not an escape
    assert rig.bus.kinds("bypass") == [] and rig.bypassed == 0
    await rig.advance(11000)
    assert not any(e.marker == LINE_CHECK_MARKER for e in rig.log if e.op == "send")
