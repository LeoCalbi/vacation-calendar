"""Views of a ledger: the monthly tables, CSV rows and an iCalendar file of the leaves."""

import csv
import datetime as dt
import io
from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal

from icalendar import Calendar, Event
from rich import box
from rich.table import Table
from rich.text import Text

from vacation_calendar.dates import effective
from vacation_calendar.ledger import Ledger, MonthRow, Notice
from vacation_calendar.models import Bucket, Config, LeaveState, Unit, YearPlan

_CENT = Decimal("0.01")

_BEFORE_CLOSING = ("closures", "adjustment")
_AFTER_CLOSING = ("payslip", "delta")
"""Optional columns, shown only when some month in the table has a value in them."""

_HEADERS = {
    "opening": "Open",
    "accrued": "Accrued",
    "taken": "Taken",
    "planned": "Planned",
    "closures": "Closures",
    "adjustment": "Adjust",
    "closing": "Close",
    "payslip": "Payslip",
    "delta": "Delta",
}

CSV_FIELDS = (
    "bucket",
    "unit",
    "month",
    "opening",
    "accrued",
    "taken",
    "planned",
    "closures",
    "adjustment",
    "closing",
    "closing_days",
    "payslip",
    "delta",
)


def amount(value: Decimal | None) -> str:
    """Format an amount with two decimals, rounding half up; an empty string for None."""
    return "" if value is None else str(value.quantize(_CENT, ROUND_HALF_UP))


def closing_days(row: MonthRow, bucket: Bucket, config: Config) -> Decimal | None:
    """Return an hours bucket's closing in days of the usual length on the month's last day.

    Returns None for buckets that already count days.
    """
    if bucket.unit is not Unit.HOURS:
        return None
    entry = effective(config.calendar.schedule, _month_end(row.month))
    if entry is None:
        return None
    return row.closing / entry.hours.usual_day()


def bucket_table(
    key: str, bucket: Bucket, rows: Sequence[MonthRow], config: Config, *, lots: bool = False
) -> Table:
    """Return the month-by-month table of one bucket, amounts in its unit with two decimals."""
    hours = bucket.unit is Unit.HOURS
    before = [name for name in _BEFORE_CLOSING if any(getattr(row, name) for row in rows)]
    after = [name for name in _AFTER_CLOSING if any(getattr(row, name) for row in rows)]
    table = Table(
        title=f"{bucket.name} ({key}, {bucket.unit.value.replace('_', ' ')})", box=box.SIMPLE_HEAD
    )
    table.add_column("Month", no_wrap=True)
    for name in ("opening", "accrued", "taken", "planned", *before, "closing"):
        table.add_column(_HEADERS[name], justify="right", no_wrap=True)
    if hours:
        table.add_column("Days", justify="right", no_wrap=True)
    for name in after:
        table.add_column(_HEADERS[name], justify="right", no_wrap=True)
    if lots:
        table.add_column("Lots", no_wrap=True)
    for row in rows:
        closing = Text(amount(row.closing), style="red" if row.closing < 0 else "")
        cells: list[str | Text] = [f"{row.month:%Y-%m}"]
        cells += [amount(getattr(row, name)) for name in ("opening", "accrued", "taken", "planned")]
        cells += [amount(getattr(row, name)) for name in before]
        cells.append(closing)
        if hours:
            cells.append(amount(closing_days(row, bucket, config)))
        cells += [amount(getattr(row, name)) for name in after]
        if lots:
            cells.append("\n".join(f"{year}: {amount(value)}" for year, value in row.lots.items()))
        table.add_row(*cells)
    return table


def absence_table(ledger: Ledger, config: Config, first: dt.date, last: dt.date) -> Table | None:
    """Return the working days of each absence by month from `first` to `last`, if any."""
    keys = [
        key
        for key, by_month in ledger.absences.items()
        if any(first <= month <= last for month in by_month)
    ]
    if not keys:
        return None
    table = Table(title="Other absences (days)", box=box.SIMPLE_HEAD)
    table.add_column("Month", no_wrap=True)
    for key in keys:
        table.add_column(config.absences[key].name, justify="right", no_wrap=True)
    months = sorted({month for key in keys for month in ledger.absences[key]})
    for month in (month for month in months if first <= month <= last):
        cells = [amount(ledger.absences[key].get(month)) for key in keys]
        table.add_row(f"{month:%Y-%m}", *cells)
    return table


def notice_lines(notices: Iterable[Notice]) -> list[str]:
    """Return one line per notice, dated."""
    return [f"{notice.day}  {notice.message}" for notice in notices]


def csv_text(ledger: Ledger, config: Config, first: dt.date, last: dt.date) -> str:
    """Return the ledger rows of months `first` to `last` as CSV, one row per bucket and month."""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, CSV_FIELDS, lineterminator="\n")
    writer.writeheader()
    for key, rows in ledger.rows.items():
        bucket = config.buckets[key]
        for row in rows:
            if not first <= row.month <= last:
                continue
            writer.writerow(
                {
                    "bucket": key,
                    "unit": bucket.unit.value,
                    "month": f"{row.month:%Y-%m}",
                    **{name: amount(getattr(row, name)) for name in CSV_FIELDS[3:10]},
                    "closing_days": amount(closing_days(row, bucket, config)),
                    "payslip": amount(row.payslip),
                    "delta": amount(row.delta),
                }
            )
    return buffer.getvalue()


def leaves_ics(
    plans: Sequence[YearPlan], config: Config, first: dt.date, last: dt.date, *, stamp: dt.datetime
) -> bytes:
    """Return the leaves that touch months `first` to `last` as an iCalendar file of all-day events.

    `stamp` is the export time.
    """
    calendar = Calendar()
    calendar.add("prodid", "-//vacation-calendar//EN")
    calendar.add("version", "2.0")
    for plan in plans:
        for index, leave in enumerate(plan.leaves, start=1):
            if leave.last < first or leave.first > _month_end(last):
                continue
            kind = config.buckets.get(leave.bucket) or config.absences[leave.bucket]
            event = Event()
            event.add("uid", f"{plan.year}-{index}-{leave.first:%Y%m%d}@vacation-calendar")
            event.add("dtstamp", stamp)
            event.add("dtstart", leave.first)
            event.add("dtend", leave.last + dt.timedelta(days=1))
            planned = " (planned)" if leave.state is LeaveState.PLANNED else ""
            event.add("summary", f"{kind.name}{planned}")
            if leave.hours is not None:
                event.add("description", f"{amount(leave.hours)} h a day")
            event.add("transp", "OPAQUE")
            calendar.add_component(event)
    ical: bytes = calendar.to_ical()
    return ical


def _month_end(month: dt.date) -> dt.date:
    following = month.replace(day=28) + dt.timedelta(days=4)
    return following - dt.timedelta(days=following.day)
