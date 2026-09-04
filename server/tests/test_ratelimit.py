import pytest

from app.errors import ApiError
from app.ratelimit import RateLimiter


def test_rate_limiter_blocks_after_limit():
    rl = RateLimiter()
    for _ in range(3):
        rl.check("k", limit=3, per_seconds=60)
    with pytest.raises(ApiError) as ei:
        rl.check("k", limit=3, per_seconds=60)
    assert ei.value.code == "RATE_LIMITED"


def test_rate_limiter_window_expires():
    now = [1000.0]
    rl = RateLimiter(clock=lambda: now[0])
    rl.check("k", limit=1, per_seconds=10)
    now[0] += 11
    rl.check("k", limit=1, per_seconds=10)
