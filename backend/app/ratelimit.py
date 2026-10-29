"""In-process sliding-window rate limiter (guest sign-in, ADR 0034).

ponytail: counters live in this process; several API workers or hosts each count on their own.
Move them to Valkey (a sorted set per key) when the API runs more than one process.
"""

import time
from collections import deque
from collections.abc import Callable
from threading import Lock

WINDOW_S = 60.0
MAX_KEYS = 10_000  # above this, keys with no hit inside the window are dropped


class SlidingWindow:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock  # tests set a fixed clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = Lock()

    def hit(self, key: str, limit: int) -> float | None:
        """Count one attempt for `key`. None when it is within `limit` per WINDOW_S, else the
        seconds until the oldest counted attempt leaves the window (the refused one is not
        counted, so a client that waits gets in)."""
        t = self.clock()
        with self._lock:
            if len(self._hits) > MAX_KEYS:
                self._hits = {k: q for k, q in self._hits.items() if q and q[-1] > t - WINDOW_S}
            q = self._hits.setdefault(key, deque())
            while q and q[0] <= t - WINDOW_S:
                q.popleft()
            if len(q) >= limit:
                return q[0] + WINDOW_S - t
            q.append(t)
            return None

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITER = SlidingWindow()
