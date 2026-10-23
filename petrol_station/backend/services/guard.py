"""Protection of the API against abuse, per client IP (in memory, one process).

- Every /api request counts towards API_RATE_LIMIT per minute; going over it returns 429.
- A device that keeps going over the limit (API_STRIKES_TO_BLOCK times in 10 minutes) or that
  gets many passwords wrong across accounts (FAILED_LOGINS_TO_BLOCK in 10 minutes) is blocked
  for IP_BLOCK_MINUTES: every API request from it is refused, the attempt is written to the
  audit log and the manager gets a SECURITY warning.

Blocks end by themselves. Real addresses only come from client_ip() (proxy headers are only
trusted with TRUST_PROXY), so a client cannot dodge this by faking a header.
"""
import threading
import time

from flask import current_app

from . import ratelimit

STRIKE_WINDOW = 600
_blocked = {}            # ip -> (unblock_at monotonic, reason)
_lock = threading.Lock()


def blocked_for(ip):
    """Seconds this IP is still blocked for (0 = not blocked)."""
    if not ip:
        return 0
    with _lock:
        entry = _blocked.get(ip)
        if entry is None:
            return 0
        left = entry[0] - time.monotonic()
        if left <= 0:
            _blocked.pop(ip, None)
            return 0
        return int(left) + 1


def block(ip, reason, station_id=None, username=None):
    minutes = current_app.config["IP_BLOCK_MINUTES"]
    with _lock:
        already = ip in _blocked and _blocked[ip][0] > time.monotonic()
        _blocked[ip] = (time.monotonic() + minutes * 60, reason)
    if already:
        return
    from ..extensions import db
    from ..models import Station
    from . import alerts, audit
    sid = station_id or db.session.query(Station.id).order_by(Station.id).scalar()
    if sid is None:
        return
    audit.log(sid, None, "BLOCKED DEVICE", reason=f"{ip} blocked for {minutes} min: {reason}")
    alerts.raise_alert(sid, "SECURITY", "DEVICE BLOCKED — POSSIBLE ATTACK",
                       f"The device at {ip} was blocked for {minutes} minutes: {reason}."
                       + (f" Last username tried: {username}." if username else "")
                       + " Nothing needs to be done unless this was one of your own devices.",
                       severity="CRITICAL", dedupe_key=f"BLOCKED_IP:{ip}")
    db.session.commit()


def check_api_request(ip):
    """Called before every /api request. Returns (status, message, retry_after) to refuse it, or None."""
    left = blocked_for(ip)
    if left:
        return 403, (f"This device is blocked for {max(1, left // 60)} more minute(s) because of too many "
                     "suspicious requests. Ask the manager if this is a mistake."), left
    cfg = current_app.config
    wait = ratelimit.hit(f"api:{ip}", cfg["API_RATE_LIMIT"], 60)
    if wait:
        if ratelimit.hit(f"strike:{ip}", cfg["API_STRIKES_TO_BLOCK"], STRIKE_WINDOW):
            block(ip, "far too many requests, again and again")
        return 429, f"Too many requests from this device. Wait {wait} seconds and try again.", wait
    return None


def failed_login(ip, station_id=None, username=None):
    """Count a wrong password from this IP; many across accounts blocks the device."""
    if ratelimit.hit(f"loginfail:{ip}", current_app.config["FAILED_LOGINS_TO_BLOCK"], STRIKE_WINDOW):
        block(ip, "many wrong passwords on different accounts", station_id, username)


def reset():
    with _lock:
        _blocked.clear()
