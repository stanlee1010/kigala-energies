"""Shared attack-protection state, used when the app runs as several copies (Vercel).

The station computer runs one process and keeps these counts in memory instead
(see services/ratelimit.py, SHARED_RATE_LIMITS).
"""
from ..extensions import db


class RateCounter(db.Model):
    """How many requests one key (e.g. "api:<ip>") made in one fixed time window."""

    __tablename__ = "rate_counters"

    key = db.Column(db.String(120), primary_key=True)
    window_start = db.Column(db.BigInteger, primary_key=True)    # unix seconds, start of the window
    count = db.Column(db.Integer, nullable=False, default=0)


class IpBlock(db.Model):
    """A device (IP address) refused on every API request until blocked_until."""

    __tablename__ = "ip_blocks"

    ip = db.Column(db.String(64), primary_key=True)
    blocked_until = db.Column(db.BigInteger, nullable=False)     # unix seconds
    reason = db.Column(db.String(255), nullable=False)
