"""Distributed token-bucket rate limiter backed by Redis.

Used to throttle outbound provider traffic *before* the provider rejects it
(e.g. Meta's per-number throughput). The bucket update is a single Lua script,
so concurrent workers on many hosts share one consistent budget.
"""

from dataclasses import dataclass
from functools import lru_cache

import redis
from django.conf import settings

_TOKEN_BUCKET_LUA = """
local key = KEYS[1]
local rate = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local requested = tonumber(ARGV[3])
-- Redis server clock: consistent across worker hosts with skewed clocks.
local t = redis.call('TIME')
local now = tonumber(t[1]) + tonumber(t[2]) / 1000000

local state = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(state[1])
local ts = tonumber(state[2])
if tokens == nil then
  tokens = capacity
  ts = now
end

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * rate)

local allowed = 0
local wait = 0
if tokens >= requested then
  tokens = tokens - requested
  allowed = 1
else
  wait = (requested - tokens) / rate
end

redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('EXPIRE', key, math.ceil(capacity / rate) + 1)
return {allowed, tostring(wait)}
"""


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_seconds: float


@lru_cache(maxsize=1)
def _client() -> redis.Redis:
    return redis.Redis.from_url(
        settings.RATE_LIMIT_REDIS_URL, socket_connect_timeout=2, socket_timeout=2
    )


class TokenBucket:
    def __init__(self, key: str, *, rate_per_second: float, burst: float | None = None):
        if rate_per_second <= 0:
            raise ValueError("rate_per_second must be positive")
        self.key = f"ratelimit:{key}"
        self.rate = rate_per_second
        self.capacity = burst if burst is not None else rate_per_second
        self._script = _client().register_script(_TOKEN_BUCKET_LUA)

    def acquire(self, tokens: float = 1) -> RateDecision:
        allowed, wait = self._script(keys=[self.key], args=[self.rate, self.capacity, tokens])
        return RateDecision(allowed=bool(int(allowed)), retry_after_seconds=float(wait))
