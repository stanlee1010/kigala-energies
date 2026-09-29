"""First-Wednesday reporting periods.

A reporting period starts on the first Wednesday of a month and runs until the
day before the first Wednesday of the next month. The first Wednesday is always
calculated, never entered by hand.
"""
from datetime import date, timedelta

WEDNESDAY = 2  # date.weekday(): Monday=0


def first_wednesday(year, month):
    first = date(year, month, 1)
    return first + timedelta(days=(WEDNESDAY - first.weekday()) % 7)


def _shift_month(year, month, delta):
    index = year * 12 + (month - 1) + delta
    return index // 12, index % 12 + 1


def is_first_wednesday(day):
    return day == first_wednesday(day.year, day.month)


def period_bounds(day):
    """Return (start, end) of the reporting period containing `day`."""
    this_fw = first_wednesday(day.year, day.month)
    if day >= this_fw:
        start = this_fw
        ny, nm = _shift_month(day.year, day.month, 1)
        end = first_wednesday(ny, nm) - timedelta(days=1)
    else:
        py, pm = _shift_month(day.year, day.month, -1)
        start = first_wednesday(py, pm)
        end = this_fw - timedelta(days=1)
    return start, end


def previous_period_bounds(day):
    start, _ = period_bounds(day)
    return period_bounds(start - timedelta(days=1))


def period_label(start):
    return start.strftime("%B %Y")
