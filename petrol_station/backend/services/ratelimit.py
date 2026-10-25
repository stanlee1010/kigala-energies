"""Rate limiter used for logins and every API route, keyed by client IP.

Two stores:
- in memory (sliding window): the station computer, which runs as ONE process, so memory is
  shared by all requests;
- in the database (fixed windows, table rate_counters): when SHARED_RATE_LIMITS is on, e.g. on
  Vercel, where many copies of the app run at once and each has its own memory. Every copy then
  counts in the same place, so an attacker cannot spread requests over copies.

Counts are written on their own connection, so they are kept even when the request itself fails
and is rolled back. If the database cannot be reached the request is allowed (fail open) and the
problem is logged, so a database hiccup never locks everyone out.
"""
import logging
import random
import threading
import time
from collections import defaultdict, deque

from flask import current_app, has_app_context

log = logging.getLogger(__name__)
_hits = defaultdict(deque)
_lock = threading.Lock()


def shared():
    return has_app_context() and bool(current_app.config.get("SHARED_RATE_LIMITS"))


def hit(key, limit, window_seconds):
    """Record one attempt. Returns seconds to wait if over the limit, else 0."""
    if shared():
        return _hit_db(key, limit, window_seconds)
    now = time.monotonic()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window_seconds:
            q.popleft()
        if len(q) >= limit:
            return int(window_seconds - (now - q[0])) + 1
        q.append(now)
        return 0


def _hit_db(key, limit, window_seconds):
    from ..extensions import db
    from ..models.security import RateCounter
    now = time.time()
    start = int(now // window_seconds * window_seconds)
    table = RateCounter.__table__
    try:
        with db.engine.begin() as conn:
            count = _increment(conn, table, key[:120], start)
            if random.random() < 0.01:   # now and then: forget windows older than a day
                conn.execute(table.delete().where(table.c.window_start < int(now) - 86400))
    except Exception:
        log.exception("Shared rate limit unavailable; allowing the request")
        return 0
    if count > limit:
        return int(start + window_seconds - now) + 1
    return 0


def _increment(conn, table, key, start):
    """Add one to (key, window) and return the new count, in one statement where possible."""
    dialect = conn.dialect.name
    if dialect in ("postgresql", "sqlite"):
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert
        stmt = insert(table).values(key=key, window_start=start, count=1)
        stmt = stmt.on_conflict_do_update(index_elements=["key", "window_start"],
                                          set_={"count": table.c.count + 1}).returning(table.c.count)
        return conn.execute(stmt).scalar_one()
    from sqlalchemy.dialects.mysql import insert   # MySQL: no RETURNING
    stmt = insert(table).values(key=key, window_start=start, count=1)
    conn.execute(stmt.on_duplicate_key_update(count=table.c.count + 1))
    return conn.execute(table.select().with_only_columns(table.c.count)
                        .where(table.c.key == key, table.c.window_start == start)).scalar_one()


def reset(key=None):
    with _lock:
        if key is None:
            _hits.clear()
        else:
            _hits.pop(key, None)
