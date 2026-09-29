from werkzeug.security import check_password_hash, generate_password_hash

from ..extensions import db
from .base import SerializerMixin, TimestampMixin

ROLES = ("STAFF", "MANAGER", "DIRECTOR")
# STAFF    - works at one station (users.station_id)
# MANAGER  - runs one or more stations (user_stations); users.station_id is the station being worked on now
# DIRECTOR - owner view of every station: sales/profit dashboard, adds or removes managers and stations


class Station(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "stations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(150), nullable=False)
    code = db.Column(db.String(30), nullable=False, unique=True)
    address = db.Column(db.String(255))
    phone = db.Column(db.String(40))
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class SystemSetting(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "system_settings"
    __table_args__ = (db.UniqueConstraint("station_id", "key", name="uq_setting_station_key"),)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    key = db.Column(db.String(80), nullable=False)
    value = db.Column(db.String(255), nullable=False)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))


class User(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "users"
    __table_args__ = (
        db.CheckConstraint("role IN ('STAFF','MANAGER','DIRECTOR')", name="ck_users_role"),
    )
    hidden_fields = ("password_hash", "failed_login_count", "locked_until")

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    username = db.Column(db.String(60), nullable=False, unique=True, index=True)
    full_name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(150))
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(10), nullable=False, default="STAFF")
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    must_change_password = db.Column(db.Boolean, nullable=False, default=True)
    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime)
    last_login_at = db.Column(db.DateTime)
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"))
    # The staff member's regular shift (roster); they are auto-assigned to every upcoming shift of this type.
    default_shift_definition_id = db.Column(db.Integer, db.ForeignKey("shift_definitions.id"))

    def set_password(self, raw):
        self.password_hash = generate_password_hash(raw)

    def check_password(self, raw):
        return check_password_hash(self.password_hash, raw)

    @property
    def is_manager(self):
        return self.role == "MANAGER"

    @property
    def is_director(self):
        return self.role == "DIRECTOR"

    @property
    def session_stamp(self):
        # Changes whenever the password changes, which invalidates old sessions.
        return self.password_hash[-16:]


class UserStation(db.Model):
    """Which stations a manager may run (set by the director)."""

    __tablename__ = "user_stations"
    __table_args__ = (db.UniqueConstraint("user_id", "station_id", name="uq_user_station"),)

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
