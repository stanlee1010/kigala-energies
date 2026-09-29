from ..extensions import db
from .base import LITRES, RATE, SerializerMixin, TimestampMixin, utcnow


class FuelType(TimestampMixin, SerializerMixin, db.Model):
    """Petrol and Diesel initially; more rows can be added later (e.g. Kerosene)."""

    __tablename__ = "fuel_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(20), nullable=False, unique=True)
    name = db.Column(db.String(60), nullable=False)
    unit = db.Column(db.String(10), nullable=False, default="L")
    color = db.Column(db.String(20), nullable=False, default="#2563eb")
    is_active = db.Column(db.Boolean, nullable=False, default=True)


class Tank(TimestampMixin, SerializerMixin, db.Model):
    """An underground storage tank. Pumps draw from a tank; refills go into a tank.

    The fuel in a tank is never typed in by staff: it is calculated from the
    opening balance + confirmed refills into the tank - litres sold by its pumps
    (+/- manager adjustments).
    """

    __tablename__ = "tanks"
    __table_args__ = (
        db.UniqueConstraint("station_id", "fuel_type_id", "tank_number", name="uq_tank_number"),
        db.CheckConstraint("tank_number > 0", name="ck_tank_number_positive"),
        db.CheckConstraint("capacity_litres > 0", name="ck_tank_capacity_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False, index=True)
    tank_number = db.Column(db.Integer, nullable=False)
    name = db.Column(db.String(60), nullable=False)
    capacity_litres = db.Column(LITRES, nullable=False, default=30000)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    fuel_type = db.relationship("FuelType", lazy="joined")


class Pump(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "pumps"
    __table_args__ = (
        db.UniqueConstraint("station_id", "pump_name", name="uq_pump_name"),
        db.UniqueConstraint("station_id", "fuel_type_id", "pump_number", name="uq_pump_number"),
        db.CheckConstraint("pump_number > 0", name="ck_pump_number_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False, index=True)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False, index=True)
    pump_number = db.Column(db.Integer, nullable=False)
    pump_name = db.Column(db.String(60), nullable=False)
    tank_id = db.Column(db.Integer, db.ForeignKey("tanks.id"), index=True)   # the tank this pump draws from
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    fuel_type = db.relationship("FuelType", lazy="joined")
    tank = db.relationship("Tank", lazy="joined")


class FuelPrice(SerializerMixin, db.Model):
    """Complete, append-only price history. The newest row is the current price."""

    __tablename__ = "fuel_prices"
    __table_args__ = (
        db.CheckConstraint("price_per_litre > 0", name="ck_price_positive"),
        db.CheckConstraint(
            "confirmation_status IN ('INITIAL','UPDATED','KEPT')", name="ck_price_confirmation"
        ),
        db.Index("ix_fuel_prices_lookup", "station_id", "fuel_type_id", "created_at"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False)
    price_per_litre = db.Column(RATE, nullable=False)
    previous_price = db.Column(RATE)
    confirmation_status = db.Column(db.String(12), nullable=False, default="UPDATED")
    reporting_period_id = db.Column(db.Integer, db.ForeignKey("reporting_periods.id"))
    set_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    note = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class StockThreshold(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "stock_thresholds"
    __table_args__ = (
        db.UniqueConstraint("station_id", "fuel_type_id", name="uq_threshold"),
        db.CheckConstraint("minimum_litres >= 0", name="ck_threshold_positive"),
    )

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    fuel_type_id = db.Column(db.Integer, db.ForeignKey("fuel_types.id"), nullable=False)
    minimum_litres = db.Column(LITRES, nullable=False, default=2000)
    updated_by = db.Column(db.Integer, db.ForeignKey("users.id"))


class Supplier(TimestampMixin, SerializerMixin, db.Model):
    __tablename__ = "suppliers"
    __table_args__ = (db.UniqueConstraint("station_id", "name", name="uq_supplier_name"),)

    id = db.Column(db.Integer, primary_key=True)
    station_id = db.Column(db.Integer, db.ForeignKey("stations.id"), nullable=False)
    name = db.Column(db.String(150), nullable=False)
    phone = db.Column(db.String(40))
    email = db.Column(db.String(150))
    is_active = db.Column(db.Boolean, nullable=False, default=True)
