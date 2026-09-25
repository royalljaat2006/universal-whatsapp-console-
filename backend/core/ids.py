"""Time-ordered UUIDs (RFC 9562 UUIDv7).

Primary keys are UUIDs so identifiers are safe to expose in URLs (no
enumeration) and can be generated before insert. Version 7 is time-ordered,
which keeps B-tree inserts append-mostly on high-volume tables (messages,
webhook events) instead of the random page splits caused by UUIDv4.
"""

import os
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7() -> uuid.UUID:
    """Return a UUIDv7 that is monotonic within this process."""
    global _last_ms, _counter
    with _lock:
        now_ms = time.time_ns() // 1_000_000
        if now_ms <= _last_ms:
            # Same (or regressed) millisecond: bump the 12-bit counter so ids
            # stay strictly increasing; borrow the next millisecond on overflow.
            _counter += 1
            if _counter > 0xFFF:
                _last_ms += 1
                _counter = 0
            now_ms = _last_ms
        else:
            _last_ms = now_ms
            _counter = int.from_bytes(os.urandom(2), "big") & 0x3FF
        counter = _counter

    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value = (now_ms & ((1 << 48) - 1)) << 80
    value |= 0x7 << 76  # version
    value |= counter << 64  # rand_a used as a monotonic counter
    value |= 0b10 << 62  # RFC 4122 variant
    value |= rand_b
    return uuid.UUID(int=value)
