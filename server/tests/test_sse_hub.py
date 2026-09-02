import asyncio
import json

from app.sse.hub import Hub, format_frame


def parse(frame: str) -> tuple[str, str, dict]:
    lines = [ln for ln in frame.strip().split("\n") if ln]
    d = {}
    for ln in lines:
        k, _, v = ln.partition(": ")
        d[k] = v
    return d["id"], d["event"], json.loads(d["data"])


def test_format_frame():
    f = format_frame("01A", "message.created", {"a": 1})
    assert f == 'id: 01A\nevent: message.created\ndata: {"a": 1}\n\n'


async def test_subscribe_gets_connected_then_live_events():
    hub = Hub()
    it = hub.subscribe("c1")
    first = await it.__anext__()
    _, ev, data = parse(first)
    assert ev == "connected" and data["caseId"] == "c1" and "serverTime" in data

    hub.publish("c1", "case.updated", {"id": "c1"})
    hub.publish("c2", "case.updated", {"id": "c2"})  # 다른 사건
    _, ev, data = parse(await it.__anext__())
    assert ev == "case.updated" and data["id"] == "c1"
    await it.aclose()
    assert hub.subscriber_count("c1") == 0


async def test_replay_after_last_event_id():
    hub = Hub()
    a = hub.publish("c1", "message.created", {"n": 1})
    b = hub.publish("c1", "message.created", {"n": 2})
    c = hub.publish("c1", "message.created", {"n": 3})
    assert a < b < c
    it = hub.subscribe("c1", last_event_id=a)
    await it.__anext__()  # connected
    _, _, d2 = parse(await it.__anext__())
    _, _, d3 = parse(await it.__anext__())
    assert (d2["n"], d3["n"]) == (2, 3)
    await it.aclose()


async def test_buffer_prunes_old_events():
    now = [1000.0]
    hub = Hub(clock=lambda: now[0])
    old = hub.publish("c1", "x", {"n": 1})
    now[0] += 301
    hub.publish("c1", "x", {"n": 2})
    it = hub.subscribe("c1", last_event_id=old)
    await it.__anext__()
    _, _, d = parse(await it.__anext__())
    assert d["n"] == 2
    await it.aclose()


async def test_keepalive_when_idle():
    hub = Hub(keepalive_seconds=0.01)
    it = hub.subscribe("c1")
    await it.__anext__()
    frame = await asyncio.wait_for(it.__anext__(), timeout=1)
    assert frame == ": keepalive\n\n"
    await it.aclose()
