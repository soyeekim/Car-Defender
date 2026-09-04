import asyncio
import json

import pytest

from app.sse.hub import QUEUE_MAXSIZE, Hub, format_frame


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


async def test_replay_does_not_duplicate_on_race_with_live_publish():
    hub = Hub()
    a = hub.publish("c1", "x", {"n": 1})
    it = hub.subscribe("c1", last_event_id=a)
    await it.__anext__()  # connected
    hub.publish("c1", "x", {"n": 9})  # race: lands in buffer + queue before replay drains

    seen = []
    while True:
        _, _, d = parse(await asyncio.wait_for(it.__anext__(), timeout=1))
        seen.append(d)
        if d.get("n") == 9:
            break
    assert sum(1 for d in seen if d.get("n") == 9) == 1

    hub.publish("c1", "x", {"n": 10})
    _, _, d = parse(await asyncio.wait_for(it.__anext__(), timeout=1))
    assert d["n"] == 10
    await it.aclose()


async def test_prune_removes_empty_buffer_key():
    now = [1000.0]
    hub = Hub(clock=lambda: now[0], keepalive_seconds=0.01)
    hub.publish("c1", "x", {"n": 1})
    now[0] += 301
    hub.publish("c2", "x", {"n": 2})  # prunes only c2's own key
    assert hub.buffer_count("c1") == 1
    assert "c1" in hub._buffers

    it = hub.subscribe("c1", last_event_id="0" * 26)  # 어떤 id 보다도 작은 유효한 ULID
    await it.__anext__()  # connected
    # generator resumes past connected here: prunes c1 (all stale), replays
    # nothing, then idles into the keepalive loop
    frame = await asyncio.wait_for(it.__anext__(), timeout=1)
    assert frame == ": keepalive\n\n"
    assert hub.buffer_count("c1") == 0
    assert "c1" not in hub._buffers
    await it.aclose()


async def test_malformed_last_event_id_is_ignored():
    hub = Hub()
    it = hub.subscribe("c1", last_event_id="zzz")
    _, ev, _ = parse(await it.__anext__())
    assert ev == "connected"
    hub.publish("c1", "x", {"n": 1})
    _, ev, d = parse(await asyncio.wait_for(it.__anext__(), timeout=1))
    assert ev == "x" and d["n"] == 1
    await it.aclose()


async def test_slow_subscriber_is_dropped_when_queue_overflows():
    hub = Hub()
    it = hub.subscribe("c1")
    await it.__anext__()  # connected, 이후로는 읽지 않는 느린 구독자
    for n in range(QUEUE_MAXSIZE + 44):
        hub.publish("c1", "x", {"n": n})
    assert hub.subscriber_count("c1") == 0
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(it.__anext__(), timeout=1)


async def test_drop_clears_buffer_and_closes_subscribers():
    hub = Hub()
    hub.publish("c1", "x", {"n": 1})
    it = hub.subscribe("c1")
    await it.__anext__()  # connected
    hub.drop("c1")
    assert hub.buffer_count("c1") == 0
    assert hub.subscriber_count("c1") == 0
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(it.__anext__(), timeout=1)
