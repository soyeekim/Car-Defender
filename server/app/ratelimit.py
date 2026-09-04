import time
from collections import defaultdict, deque
from collections.abc import Callable

from app.errors import ApiError


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic):
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, *, limit: int, per_seconds: int) -> None:
        now = self._clock()
        q = self._hits[key]
        while q and now - q[0] > per_seconds:
            q.popleft()
        if len(q) >= limit:
            raise ApiError("RATE_LIMITED")
        q.append(now)

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()
