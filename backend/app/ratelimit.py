"""Per-user sliding-window limiter on analysed posts.

In-process only: with several workers each enforces its own window. Put a shared limiter
(e.g. at the reverse proxy) in front for multi-worker deployments.
"""
import threading
import time
from collections import defaultdict, deque


class SlidingWindowLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit, self.window = limit, window_seconds
        self._events: dict[object, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def try_acquire(self, key, cost: int = 1) -> tuple[bool, float]:
        """Returns (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            q = self._events[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) + cost > self.limit:
                retry = self.window - (now - q[0]) if q else self.window
                return False, max(retry, 1.0)
            q.extend([now] * cost)
            return True, 0.0
