import asyncio

import app.db as appdb
from app.sse.hub import hub


class SseProbe:
    """앱을 ASGI로 직접 호출해 SSE 청크를 하나씩 받는다."""

    def __init__(self, app, path: str, headers: dict | None = None, query: str = ""):
        self.app, self.path, self.query = app, path, query
        self.headers = headers or {}
        self.status = None
        self.response_headers = {}
        self.chunks: asyncio.Queue[bytes] = asyncio.Queue()
        self._task = None

    async def _receive(self):
        await asyncio.sleep(3600)
        return {"type": "http.disconnect"}

    async def _send(self, message):
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.response_headers = {k.decode(): v.decode() for k, v in message["headers"]}
        elif message["type"] == "http.response.body" and message.get("body"):
            await self.chunks.put(message["body"])

    async def start(self):
        scope = {
            "type": "http", "http_version": "1.1", "method": "GET", "scheme": "http",
            "path": self.path, "raw_path": self.path.encode(), "query_string": self.query.encode(),
            "headers": [(k.lower().encode(), v.encode()) for k, v in self.headers.items()],
            "server": ("test", 80), "client": ("127.0.0.1", 1),
        }
        self._task = asyncio.create_task(self.app(scope, self._receive, self._send))
        return self

    async def next_frame(self, timeout: float = 5.0) -> str:
        return (await asyncio.wait_for(self.chunks.get(), timeout)).decode()

    async def close(self):
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass


async def test_events_stream_connected_then_event(app, auth_headers, case_id):
    probe = await SseProbe(app, f"/api/v1/cases/{case_id}/events", auth_headers).start()
    first = await probe.next_frame()
    assert probe.status == 200 and probe.response_headers["content-type"].startswith("text/event-stream")
    assert "event: connected" in first
    hub.publish(case_id, "case.updated", {"id": case_id})
    assert "event: case.updated" in await probe.next_frame()
    await probe.close()
    assert hub.subscriber_count(case_id) == 0


async def test_events_stream_does_not_hold_db_session(app, auth_headers, case_id):
    probe = await SseProbe(app, f"/api/v1/cases/{case_id}/events", auth_headers).start()
    assert "event: connected" in await probe.next_frame()
    # 스트림이 살아 있는 동안 커넥션 풀을 점유하면 안 된다
    assert appdb.engine().pool.checkedout() == 0
    await probe.close()


async def test_events_accepts_query_token(app, auth_headers, case_id):
    token = auth_headers["Authorization"].split()[1]
    probe = await SseProbe(app, f"/api/v1/cases/{case_id}/events", query=f"access_token={token}").start()
    assert (await probe.next_frame()).startswith("id: ")
    await probe.close()


async def test_events_replays_after_last_event_id(app, auth_headers, case_id):
    first_id = hub.publish(case_id, "case.updated", {"n": 1})
    hub.publish(case_id, "case.updated", {"n": 2})
    probe = await SseProbe(app, f"/api/v1/cases/{case_id}/events", {**auth_headers, "Last-Event-ID": first_id}).start()
    await probe.next_frame()  # connected
    assert '"n": 2' in await probe.next_frame()
    await probe.close()


async def test_events_requires_auth(client, case_id):
    res = await client.get(f"/cases/{case_id}/events")
    assert res.status_code == 401
