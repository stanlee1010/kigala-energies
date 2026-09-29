"""Simple in-memory sliding-window rate limiter (per process).

Used for the login endpoint, keyed by client IP. Together with the per-account
lock-out (5 wrong passwords -> 10 minutes) it stops password guessing both on one
account and across many usernames. The app runs as one process (Waitress /
gunicorn -w 1), so memory is shared by all requests.
"""
import threading
import time
from collections import defaultdict, deque

_hits = defaultdict(deque)
_lock = threading.Lock()


def hit(key, limit, window_seconds):
    """Record one attempt. Returns seconds to wait if over the limit, else 0."""
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            return int(window_seconds - (now - q[0])) + 1
        q.append(now)
        return 0


def reset(key=None):
    with _lock:
        if key is None:
            _hits.clear()
        else:
            _hits.pop(key, None)
