import asyncio
import json
import re
import time
from collections import defaultdict, deque
from collections.abc import AsyncIterator, Callable

from app.clock import now_utc, to_kst_iso
from app.ids import new_id

KEEPALIVE_SECONDS = 15
BUFFER_SECONDS = 300
QUEUE_MAXSIZE = 256

# ULID(Crockford base32) 형식만 Last-Event-ID 로 인정한다.
EVENT_ID_RE = re.compile(r"^[0-9A-HJKMNP-TV-Z]{26}$")

_CLOSE = None  # 구독을 끊을 때 큐에 넣는 표식


def format_frame(event_id: str, event: str, data: dict) -> str:
    return f"id: {event_id}\nevent: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class Hub:
    def __init__(self, clock: Callable[[], float] = time.monotonic, keepalive_seconds: float = KEEPALIVE_SECONDS):
        self._clock = clock
        self._keepalive = keepalive_seconds
        self._buffers: dict[str, deque[tuple[str, float, str]]] = defaultdict(deque)
        self._subs: dict[str, set[asyncio.Queue[tuple[str, str] | None]]] = defaultdict(set)
        self._dropped: set[asyncio.Queue[tuple[str, str] | None]] = set()

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

    def _unsubscribe(self, case_id: str, q: asyncio.Queue) -> None:
        subs = self._subs.get(case_id)
        if subs is None:
            return
        subs.discard(q)
        if not subs:
            self._subs.pop(case_id, None)

    def _drop_subscriber(self, case_id: str, q: asyncio.Queue) -> None:
        """구독을 끊고, 대기 중인 제너레이터가 다음 차례에 종료하도록 깨운다."""
        self._unsubscribe(case_id, q)
        self._dropped.add(q)
        try:
            q.put_nowait(_CLOSE)
        except asyncio.QueueFull:
            pass  # 큐가 꽉 찼으면 제너레이터가 다음 get() 뒤 _dropped 를 보고 끝낸다

    def publish(self, case_id: str, event: str, data: dict) -> str:
        event_id = new_id()
        frame = format_frame(event_id, event, data)
        self._prune(case_id)
        self._buffers[case_id].append((event_id, self._clock(), frame))
        for q in list(self._subs.get(case_id, ())):
            try:
                q.put_nowait((event_id, frame))
            except asyncio.QueueFull:
                # 읽지 않는 구독자가 메모리를 무한히 먹지 않도록 끊는다
                self._drop_subscriber(case_id, q)
        return event_id

    def subscriber_count(self, case_id: str) -> int:
        return len(self._subs.get(case_id, ()))

    def drop(self, case_id: str) -> None:
        """사건이 사라졌을 때 버퍼를 비우고 구독자를 모두 끊는다."""
        self._buffers.pop(case_id, None)
        for q in list(self._subs.get(case_id, ())):
            self._drop_subscriber(case_id, q)

    def reset(self) -> None:
        self._buffers.clear()
        self._subs.clear()
        self._dropped.clear()

    async def subscribe(self, case_id: str, last_event_id: str | None = None) -> AsyncIterator[str]:
        if last_event_id is not None and not EVENT_ID_RE.match(last_event_id):
            last_event_id = None  # 형식이 틀린 헤더는 없는 것으로 본다
        q: asyncio.Queue[tuple[str, str] | None] = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
        self._subs[case_id].add(q)
        try:
            yield format_frame(new_id(), "connected", {"caseId": case_id, "serverTime": to_kst_iso(now_utc())})
            last_sent_id: str | None = None  # 실제로 내보낸 id 만 기록한다
            if last_event_id is not None:
                self._prune(case_id)
                for event_id, _, frame in list(self._buffers.get(case_id, ())):
                    if event_id > last_event_id:
                        yield frame
                        last_sent_id = event_id
            while True:
                try:
                    item = await asyncio.wait_for(q.get(), timeout=self._keepalive)
                except TimeoutError:
                    if q in self._dropped:
                        return
                    yield ": keepalive\n\n"
                    continue
                if item is _CLOSE or q in self._dropped:
                    return
                event_id, frame = item
                if last_sent_id is not None and event_id <= last_sent_id:
                    continue
                yield frame
                last_sent_id = event_id
        finally:
            self._dropped.discard(q)
            self._unsubscribe(case_id, q)


hub = Hub()
