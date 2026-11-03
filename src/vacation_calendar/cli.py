"""The `vacation-calendar` command: set up a data folder, record leaves and payslips, report."""

import datetime as dt
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from pydantic import ValidationError
from rich import box
from rich.console import Console
from rich.table import Table

from vacation_calendar import report, storage
from vacation_calendar.dates import effective
from vacation_calendar.holiday_set import CountryBase
from vacation_calendar.ledger import Ledger, build_ledger
from vacation_calendar.models import (
    Config,
    Leave,
    LeaveState,
    PayslipEntry,
    PayslipMode,
    Unit,
    YearPlan,
)
from vacation_calendar.work_calendar import DayKind, WorkCalendar

app = typer.Typer(
    no_args_is_help=True,
    help="Plan time off across years: holidays, accrual, leaves and payslip checks.",
)
_out = Console(highlight=False)
_err = Console(stderr=True, highlight=False)

_MONTH = re.compile(r"(\d{4})(?:-(\d{2}))?")
_UNITS = {Unit.HOURS: "h", Unit.DAYS: "days", Unit.CALENDAR_DAYS: "calendar days"}


class ExportFormat(StrEnum):
    """What `export` writes."""

    CSV = "csv"
    ICS = "ics"


def _date(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError:
        msg = f"{text!r} is not a date like 2026-08-17"
        raise ValueError(msg) from None


def _decimal(text: str) -> Decimal:
    try:
        return Decimal(text)
    except InvalidOperation:
        msg = f"{text!r} is not a number"
        raise ValueError(msg) from None


DataDir = Annotated[
    Path | None,
    typer.Option(
        "--data-dir",
        file_okay=False,
        help=f"Data folder. Default: ${storage.ENV_VAR}, else the user data folder.",
        show_default=False,
    ),
]
Period = Annotated[
    str | None,
    typer.Argument(
        help="A year (2026), or a range of years or months (2026..2027, 2026-03..2026-12).",
        show_default=False,
    ),
]
BucketOption = Annotated[
    str | None,
    typer.Option(
        "--bucket",
        "-b",
        help="Bucket key; may be left out when there is only one.",
        show_default=False,
    ),
]


@app.callback()
def main(ctx: typer.Context, data_dir: DataDir = None) -> None:
    """Plan time off across years: holidays, accrual, leaves and payslip checks."""
    ctx.obj = storage.data_dir(data_dir)


@app.command()
def init(
    ctx: typer.Context,
    country: Annotated[
        str | None,
        typer.Option(
            help="Country code of the public holidays, such as IT or US.", show_default=False
        ),
    ] = None,
    subdiv: Annotated[
        str | None,
        typer.Option(
            help="Code of a region of the country, such as BO (Bologna) or BY (Bavaria).",
            show_default=False,
        ),
    ] = None,
    source: Annotated[
        Path | None,
        typer.Option(
            "--from",
            file_okay=False,
            help="Copy this data folder instead, such as one of the repository's examples.",
            show_default=False,
        ),
    ] = None,
) -> None:
    """Create the data folder: a commented template for a country, or a copy of another folder."""
    with _reported():
        if (country is None) == (source is None) or (subdiv is not None and country is None):
            msg = "give --country (and optionally --subdiv), or --from FOLDER"
            raise ValueError(msg)
        if source is not None:
            written = storage.copy_folder(source, ctx.obj)
        else:
            base = CountryBase(country=(country or "").upper(), subdiv=subdiv and subdiv.upper())
            written = [storage.write_template(ctx.obj, base, _today().year)]
    for path in written:
        _say(f"wrote {path}")
    _say("Edit config.toml to match your contract, then add leaves with: vacation-calendar add")


@app.command("holidays")
def holidays_command(
    ctx: typer.Context,
    year: Annotated[
        int | None, typer.Argument(help="Default: this year.", show_default=False)
    ] = None,
) -> None:
    """List the holidays, closure days and make-up working days of a year."""
    year = year or _today().year
    with _reported():
        config = storage.read_config(ctx.obj)
        calendar = WorkCalendar(config.calendar)
        holidays = calendar.holidays(year)
        table = Table(
            "Date", "Day", "Kind", "Name", title=f"Days off in {year}", box=box.SIMPLE_HEAD
        )
        start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
        for date in (start + dt.timedelta(days=offset) for offset in range((end - start).days + 1)):
            if date in holidays:
                table.add_row(f"{date}", f"{date:%a}", "holiday", "; ".join(holidays[date]))
                continue
            entry = effective(config.calendar.schedule, date)
            if entry is None:
                continue
            day = calendar.day(date)
            if day.closure is not None:
                kind = f"closure, uses {day.closure.bucket}"
                table.add_row(f"{date}", f"{date:%a}", kind, day.closure.name)
            elif day.kind is DayKind.WORKING and not entry.hours.on(date):
                table.add_row(f"{date}", f"{date:%a}", "make-up working day", "")
    _show(table)


@app.command()
def add(
    ctx: typer.Context,
    start: Annotated[str, typer.Argument(metavar="DATE", help="The day, or the first of a range.")],
    end: Annotated[
        str | None,
        typer.Argument(metavar="[END]", help="The last day of a range.", show_default=False),
    ] = None,
    bucket: BucketOption = None,
    hours: Annotated[
        str | None,
        typer.Option(
            "--hours",
            metavar="HOURS",
            help="Hours on each working day. Default: the whole day.",
            show_default=False,
        ),
    ] = None,
    taken: Annotated[
        bool, typer.Option("--taken", help="Record it as taken, not planned.")
    ] = False,
) -> None:
    """Add a leave on one day or on a range of days; rest days and holidays in it use nothing."""
    folder: Path = ctx.obj
    with _reported():
        config = storage.read_config(folder)
        key = bucket or _only_bucket(config)
        first = _date(start)
        leave = Leave(
            date=first if end is None else None,
            start=None if end is None else first,
            end=None if end is None else _date(end),
            hours=None if hours is None else _decimal(hours),
            bucket=key,
            state=LeaveState.TAKEN if taken else LeaveState.PLANNED,
        )
        year = storage.YearFile.open(folder, leave.first.year)
        year.add_leave(leave)
        config, plans = storage.check_folder(folder, year)
        year.save()
        ledger = build_ledger(config, plans, today=_today())
    _say(f"added {leave} ({leave.state.value}) from {key} to {year.path}")
    _say(_year_end(ledger, config, key, leave.first.year))


@app.command()
def take(
    ctx: typer.Context,
    day: Annotated[str, typer.Argument(metavar="DAY", help="Any day of the leave.")],
    bucket: BucketOption = None,
) -> None:
    """Mark the planned leave that includes DAY as taken."""
    folder: Path = ctx.obj
    with _reported():
        date = _date(day)
        for year in (date.year, date.year - 1):  # a leave may start in the year before
            if not (folder / f"{year}.toml").exists():
                continue
            file = storage.YearFile.open(folder, year)
            changed = file.take(date, bucket)
            if changed:
                storage.check_folder(folder, file)
                file.save()
                for leave in changed:
                    _say(f"taken: {leave} from {leave.bucket}")
                return
        suffix = f" from {bucket}" if bucket else ""
        msg = f"no planned leave{suffix} includes {date}"
        raise ValueError(msg)


@app.command()
def payslip(
    ctx: typer.Context,
    month: Annotated[str, typer.Argument(help="The payslip's month, such as 2026-09.")],
    balances: Annotated[
        list[str],
        typer.Option(
            "--balance",
            "-b",
            help="BUCKET=AMOUNT, the closing balance the payslip prints; once per bucket.",
        ),
    ],
    override: Annotated[
        bool,
        typer.Option(
            "--override", help="Re-anchor the balances on the payslip instead of only checking."
        ),
    ] = False,
) -> None:
    """Record a payslip's balances and show how they differ from the computed ones."""
    folder: Path = ctx.obj
    with _reported():
        entry = PayslipEntry.model_validate(
            {
                "month": month,
                "mode": PayslipMode.OVERRIDE if override else PayslipMode.CHECK,
                "balances": dict(_pair(text) for text in balances),
            }
        )
        file = storage.YearFile.open(folder, entry.month.year)
        file.set_payslip(entry)
        config, plans = storage.check_folder(folder, file)
        file.save()
        ledger = build_ledger(config, plans, today=_today())
    _say(f"recorded the {entry.month:%Y-%m} payslip ({entry.mode.value}) in {file.path}")
    for key in entry.balances:
        row = next(row for row in ledger.rows[key] if row.month == entry.month)
        if row.payslip is None or row.delta is None:
            continue
        computed = row.payslip - row.delta
        sign = "+" if row.delta >= 0 else ""
        unit = _UNITS[config.buckets[key].unit]
        _say(
            f"{key}: payslip {report.amount(row.payslip)} {unit}, computed"
            f" {report.amount(computed)} ({sign}{report.amount(row.delta)})"
        )


@app.command("report")
def report_command(
    ctx: typer.Context,
    period: Period = None,
    lots: Annotated[
        bool, typer.Option("--lots", help="Show which year each part of the balance accrued in.")
    ] = False,
    strict: Annotated[
        bool, typer.Option("--strict", help="Fail when a balance goes below zero.")
    ] = False,
) -> None:
    """Show each bucket month by month (default: this year), then the notices."""
    with _reported():
        today = _today()
        config, plans = storage.check_folder(ctx.obj)
        first, last = _period(period, dt.date(today.year, 1, 1), dt.date(today.year, 12, 1))
        ledger = _build(config, plans, first, last, strict=strict)
    payslips_shown = False
    for key, bucket in config.buckets.items():
        rows = [row for row in ledger.rows[key] if first <= row.month <= last]
        _show(report.bucket_table(key, bucket, rows, config, lots=lots))
        payslips_shown |= any(row.payslip is not None for row in rows)
    if config.payslip_lag and payslips_shown:
        months = f"{config.payslip_lag} month{'s' if config.payslip_lag > 1 else ''}"
        _say(f"Payslips count leave {months} late: Delta compares them with Close plus that leave.")
    after = (last.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    notices = [notice for notice in ledger.notices if first <= notice.day < after]
    if notices:
        _out.print("Notices", style="bold")
        for line in report.notice_lines(notices):
            _say(line)


@app.command()
def export(
    ctx: typer.Context,
    kind: Annotated[
        ExportFormat,
        typer.Argument(help="csv: the monthly balances; ics: the leaves as calendar events."),
    ],
    period: Period = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output", "-o", dir_okay=False, help="File to write. Default: standard output."
        ),
    ] = None,
) -> None:
    """Export the balances as CSV or the leaves as an iCalendar file (default: every year)."""
    with _reported():
        config, plans = storage.check_folder(ctx.obj)
        if not plans:
            msg = "there are no year files to export yet"
            raise ValueError(msg)
        first, last = _period(period, dt.date(plans[0].year, 1, 1), dt.date(plans[-1].year, 12, 1))
        if kind is ExportFormat.CSV:
            ledger = _build(config, plans, first, last)
            data = report.csv_text(ledger, config, first, last).encode()
        else:
            data = report.leaves_ics(plans, config, first, last, stamp=_now())
    if output is None:
        sys.stdout.flush()
        sys.stdout.buffer.write(data)
    else:
        output.write_bytes(data)
        _say(f"wrote {output}")


def _build(
    config: Config, plans: list[YearPlan], first: dt.date, last: dt.date, *, strict: bool = False
) -> Ledger:
    """Build the ledger far enough to show `first` to `last`; no year file means an empty year."""
    if not plans:
        plans = [YearPlan(year=first.year)]
    if first.year < plans[0].year:
        msg = f"the data starts in {plans[0].year}; there is no balance before it"
        raise ValueError(msg)
    until = max(last, dt.date(plans[-1].year, 12, 1))
    return build_ledger(config, plans, today=_today(), until=until, strict=strict)


def _period(text: str | None, first: dt.date, last: dt.date) -> tuple[dt.date, dt.date]:
    """Return the first and last month of `text`, or of the default `first`-`last`."""
    if text is None:
        return first, last
    start, separator, end = text.partition("..")
    first = _month(start, first=True)
    last = _month(end if separator else start, first=False)
    if last < first:
        msg = f"period {text!r} ends before it starts"
        raise ValueError(msg)
    return first, last


def _month(text: str, *, first: bool) -> dt.date:
    match = _MONTH.fullmatch(text)
    if not match:
        msg = f"{text!r} is not a year like 2026 or a month like 2026-03"
        raise ValueError(msg)
    month = int(match[2]) if match[2] else (1 if first else 12)
    return dt.date(int(match[1]), month, 1)


def _pair(text: str) -> tuple[str, str]:
    key, separator, value = text.partition("=")
    if not separator or not key.strip() or not value.strip():
        msg = f"--balance {text!r}: write BUCKET=AMOUNT"
        raise ValueError(msg)
    return key.strip(), value.strip()


def _only_bucket(config: Config) -> str:
    if len(config.buckets) == 1:
        return next(iter(config.buckets))
    msg = f"give --bucket, one of: {', '.join(config.buckets)}"
    raise ValueError(msg)


def _year_end(ledger: Ledger, config: Config, key: str, year: int) -> str:
    row = next(row for row in ledger.rows[key] if row.month == dt.date(year, 12, 1))
    unit = _UNITS[config.buckets[key].unit]
    return f"{key} at the end of {year}: {report.amount(row.closing)} {unit}"


def _show(table: Table) -> None:
    """Print `table` at its natural width, even past the terminal's, so no cell is cut."""
    unlimited = _out.options.update(max_width=10_000)
    width = max(_out.width, _out.measure(table, options=unlimited).maximum)
    Console(width=width, highlight=False).print(table)  # print() caps width at the console's


def _say(text: str) -> None:
    _out.print(text, markup=False, soft_wrap=True)


def _fail(lines: list[str]) -> NoReturn:
    for line in lines:
        _err.print(f"error: {line}", markup=False, soft_wrap=True, style="red")
    raise typer.Exit(1)


@contextmanager
def _reported() -> Iterator[None]:
    """Turn a data or validation error into a message and exit code 1."""
    try:
        yield
    except ValidationError as error:
        _fail([item["msg"].removeprefix("Value error, ") for item in error.errors()])
    except (ValueError, FileExistsError) as error:
        _fail(str(error).splitlines())


def _today() -> dt.date:
    return _now().astimezone().date()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.UTC)
