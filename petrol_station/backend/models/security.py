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


class ProcessedRequest(db.Model):
    """A save the device marked with a unique Idempotency-Key and the server has applied.

    Saved in the same transaction as the change itself, so a save is applied exactly once
    even if the device sends it again (e.g. the reply was lost on a weak connection).
    """

    __tablename__ = "processed_requests"

    key = db.Column(db.String(64), primary_key=True)
    created_at = db.Column(db.BigInteger, nullable=False)        # unix seconds


class LoginFailure(db.Model):
    """Wrong passwords for one username from one device (ip), or from all devices (ip = "*").

    Kept by USERNAME, whether or not such an account exists, so a made-up username behaves
    exactly like a real one (the answers never reveal which usernames exist), and one
    device guessing a password cannot lock the real person out on their own device.
    """

    __tablename__ = "login_failures"

    username = db.Column(db.String(60), primary_key=True)
    ip = db.Column(db.String(64), primary_key=True)
    count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)                       # UTC
    updated_at = db.Column(db.DateTime, nullable=False)          # UTC


class UserSession(db.Model):
    """One login on one device. The session cookie names it; logging out ends it on the server,
    so a copy of the cookie stops working too."""

    __tablename__ = "user_sessions"

    id = db.Column(db.String(64), primary_key=True)              # random, unguessable
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False)          # UTC
    ended_at = db.Column(db.DateTime)                            # UTC; set at logout


class TwoStepLogin(db.Model):
    """A manager's or the director's authenticator-app key (2-step login).

    enabled_at is empty while being set up (until a first correct code is typed).
    last_step: the time step of the last code used, so no code works twice.
    """

    __tablename__ = "two_step_logins"

    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    secret = db.Column(db.String(64), nullable=False)
    enabled_at = db.Column(db.DateTime)                          # UTC
    last_step = db.Column(db.BigInteger)
