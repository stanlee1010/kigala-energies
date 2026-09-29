"""Low-stock minimum per fuel (set by the manager)."""
from ..calculations import D
from ..models import StockThreshold


def minimum_for(station_id, fuel_type_id):
    row = StockThreshold.query.filter_by(station_id=station_id, fuel_type_id=fuel_type_id).first()
    return D(row.minimum_litres) if row else None
