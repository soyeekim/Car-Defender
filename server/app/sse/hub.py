import asyncio
import json
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable

from app.clock import now_utc, to_kst_iso
from app.ids import new_id

KEEPALIVE_SECONDS = 15
BUFFER_SECONDS = 300


def format_frame(event_id: str, event: str, data: dict) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Hub:
    def __init__(self, clock: Callable[[], float] = time.monotonic, keepalive_seconds: float = KEEPALIVE_SECONDS):
        self._clock = clock
        self._keepalive = keepalive_seconds
        self._buffers: dict[str, deque[tuple[str, float, str]]] = defaultdict(deque)
        self._subs: dict[str, set[asyncio.Queue[tuple[str, str]]]] = defaultdict(set)

    def _prune(self, case_id: str) -> None:
        buf = self._buffers.get(case_id)
        if not buf:
            return
        cutoff = self._clock() - BUFFER_SECONDS
        while buf and buf[0][1] < cutoff:
            buf.popleft()
        if not buf:
            self._buffers.pop(case_id, None)

    def buffer_count(self, case_id: str) -> int:
        return len(self._buffers.get(case_id, ()))

    def publish(self, case_id: str, event: str, data: dict) -> str:
        event_id = new_id()
        frame = format_frame(event_id, event, data)
        self._prune(case_id)
        self._buffers[case_id].append((event_id, self._clock(), frame))
        for q in list(self._subs.get(case_id, ())):
            q.put_nowait((event_id, frame))
        return event_id

    def subscriber_count(self, case_id: str) -> int:
        return len(self._subs.get(case_id, ()))

    def reset(self) -> None:
        self._buffers.clear()
        self._subs.clear()

    async def subscribe(self, case_id: str, last_event_id: str | None = None) -> AsyncIterator[str]:
        q: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._subs[case_id].add(q)
        try:
            yield format_frame(new_id(), "connected", {"caseId": case_id, "serverTime": to_kst_iso(now_utc())})
            last_sent_id = last_event_id
            if last_event_id is not None:
                self._prune(case_id)
                for event_id, _, frame in list(self._buffers.get(case_id, ())):
                    if event_id > last_event_id:
                        yield frame
                        last_sent_id = event_id
            while True:
                try:
                    event_id, frame = await asyncio.wait_for(q.get(), timeout=self._keepalive)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if last_sent_id is not None and event_id <= last_sent_id:
                    continue
                yield frame
                last_sent_id = event_id
        finally:
            self._subs[case_id].discard(q)
            if not self._subs[case_id]:
                del self._subs[case_id]


hub = Hub()
