"""The ledger: each bucket's balance by month, from carry-over, accrual, leaves and payslips."""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from vacation_calendar.models import (
    Bucket,
    Config,
    Leave,
    LeaveState,
    PayslipMode,
    Unit,
    YearPlan,
    check_plans,
)
from vacation_calendar.work_calendar import Day, DayKind, WorkCalendar

_ZERO = Decimal(0)
_CENT = Decimal("0.01")


class NoticeKind(StrEnum):
    """Something in the ledger worth a look; none of them stops the computation."""

    NO_CARRY_OVER = "no_carry_over"
    """The first year gives no carry-over for a bucket, so it starts from 0."""
    PAYSLIP_MISMATCH = "payslip_mismatch"
    """A payslip in check mode differs from the computed balance."""
    NEGATIVE_BALANCE = "negative_balance"
    """A bucket closes a month below 0."""
    PAST_PLANNED = "past_planned"
    """A leave still planned has ended before today: mark it taken or remove it."""
    NO_LEAVE_USED = "no_leave_used"
    """A leave falls only on rest days, holidays or closure days, so it uses nothing."""


@dataclass(frozen=True, slots=True)
class Notice:
    """One notice, dated, and tied to a bucket when it concerns one."""

    kind: NoticeKind
    day: dt.date
    message: str
    bucket: str | None = None


@dataclass(frozen=True, slots=True)
class MonthRow:
    """One bucket in one month, in the bucket's unit.

    `closing` is `opening + accrued - taken - planned - closures + adjustment`. The `adjustment`
    re-anchors the balance: a carry-over given in a year file, or a payslip in override mode.
    `delta` is the payslip balance minus the computed balance as the payslip sees it, before any
    override: the closing plus the leave of the last `Config.payslip_lag` months. `lots` splits
    the closing by the year it accrued in; leave uses the oldest first.
    """

    month: dt.date
    opening: Decimal
    accrued: Decimal
    taken: Decimal
    planned: Decimal
    closures: Decimal
    adjustment: Decimal
    closing: Decimal
    payslip: Decimal | None
    delta: Decimal | None
    lots: Mapping[int, Decimal]


@dataclass(frozen=True, slots=True)
class Ledger:
    """The month rows of every bucket, by bucket key, and the notices, by date."""

    rows: Mapping[str, tuple[MonthRow, ...]]
    notices: tuple[Notice, ...]


class NegativeBalanceError(ValueError):
    """A balance went below 0 in strict mode; `notices` lists each month it did."""

    def __init__(self, notices: list[Notice]) -> None:
        """Create the error from the negative-balance notices."""
        self.notices = tuple(notices)
        super().__init__("\n".join(notice.message for notice in notices))


@dataclass(frozen=True, slots=True)
class _Use:
    taken: Decimal = _ZERO
    planned: Decimal = _ZERO
    closures: Decimal = _ZERO

    def __add__(self, other: "_Use") -> "_Use":
        return _Use(
            self.taken + other.taken,
            self.planned + other.planned,
            self.closures + other.closures,
        )

    @property
    def total(self) -> Decimal:
        return self.taken + self.planned + self.closures


def build_ledger(
    config: Config,
    plans: Sequence[YearPlan],
    *,
    today: dt.date,
    until: dt.date | None = None,
    strict: bool = False,
) -> Ledger:
    """Compute every bucket month by month, from January of the first plan to `until`.

    `until` defaults to the end of the last plan's year; later months follow the accrual alone.
    Leaves count from the month they fall in, closures from their days. Raises `PlanError` when the
    plans do not fit `config`, and `NegativeBalanceError` in `strict` mode when a balance goes below
    0. `today` decides which planned leaves are already past.
    """
    calendar = WorkCalendar(config.calendar)
    check_plans(plans, config.buckets, calendar)
    if not plans:
        msg = "the ledger needs at least one year plan"
        raise ValueError(msg)
    by_year = {plan.year: plan for plan in plans}
    start = dt.date(min(by_year), 1, 1)
    end = (until or dt.date(max(by_year), 12, 31)).replace(day=1)
    if end < start:
        msg = f"until {until} is before the first plan year {start.year}"
        raise ValueError(msg)
    months = [(month, by_year.get(month.year)) for month in _months(start, end)]
    notices = list(_leave_notices(plans, config.buckets, calendar, today))
    use = _usage(plans, config.buckets, calendar, start, end)
    rows = {key: tuple(_bucket_rows(key, config, months, use, notices)) for key in config.buckets}
    negative = [notice for notice in notices if notice.kind is NoticeKind.NEGATIVE_BALANCE]
    if strict and negative:
        raise NegativeBalanceError(negative)
    return Ledger(rows, tuple(sorted(notices, key=lambda notice: notice.day)))


def _bucket_rows(
    key: str,
    config: Config,
    months: Sequence[tuple[dt.date, YearPlan | None]],
    use: Mapping[tuple[str, dt.date], _Use],
    notices: list[Notice],
) -> Iterator[MonthRow]:
    bucket, lag = config.buckets[key], config.payslip_lag
    lots: defaultdict[int, Decimal] = defaultdict(Decimal)
    closing: Decimal | None = None
    history: list[Decimal] = []  # each month's use, for the payslip lag
    for month, plan in months:
        given = plan.carry_over.get(key) if plan is not None and month.month == 1 else None
        adjustment = _ZERO
        if closing is None:
            opening = _ZERO if given is None else given
            _credit(lots, month.year - 1, opening)
            if given is None:
                notices.append(
                    Notice(
                        NoticeKind.NO_CARRY_OVER,
                        month,
                        f"{key}: no carry-over into {month.year}; starting from 0",
                        key,
                    )
                )
        else:
            opening = closing
            if given is not None:
                adjustment = given - opening
                _credit(lots, month.year - 1, adjustment)
        used = use.get((key, month), _Use())
        accrued = _accrued(bucket, month)
        _debit(lots, used.total, month.year)
        _credit(lots, month.year, accrued)
        closing = opening + adjustment + accrued - used.total
        history.append(used.total)
        # The leave of the last `lag` months, which the month's payslip does not count yet.
        unseen = sum(history[-lag:], _ZERO) if lag else _ZERO
        payslip = (
            next((entry for entry in plan.payslips if entry.month == month), None) if plan else None
        )
        balance = payslip.balances.get(key) if payslip is not None else None
        delta = None if balance is None else balance - (closing + unseen)
        if payslip is not None and balance is not None and delta:
            if payslip.mode is PayslipMode.OVERRIDE:
                adjustment += delta
                _credit(lots, month.year, delta)
                closing += delta
            else:
                notices.append(
                    Notice(
                        NoticeKind.PAYSLIP_MISMATCH,
                        _month_end(month),
                        f"{key}: payslip {month:%Y-%m} shows {balance}, computed {closing + unseen}"
                        f" ({delta:+})",
                        key,
                    )
                )
        if closing < 0:
            notices.append(
                Notice(
                    NoticeKind.NEGATIVE_BALANCE,
                    _month_end(month),
                    f"{key}: balance {closing} at the end of {month:%Y-%m}",
                    key,
                )
            )
        yield MonthRow(
            month=month,
            opening=opening,
            accrued=accrued,
            taken=used.taken,
            planned=used.planned,
            closures=used.closures,
            adjustment=adjustment,
            closing=closing,
            payslip=balance,
            delta=delta,
            lots={year: amount for year, amount in sorted(lots.items()) if amount},
        )


def _accrued(bucket: Bucket, month: dt.date) -> Decimal:
    """Return the monthly accrual in effect on the month's first day plus grants falling in it."""
    total = _ZERO
    monthly = bucket.accrual_on(month)
    if monthly is not None and monthly.monthly is not None:
        total += monthly.monthly
    for entry in bucket.accrual:
        if entry.yearly is None or entry.valid_from.month != month.month:
            continue
        grant = month.replace(day=entry.valid_from.day)
        if grant >= entry.valid_from and bucket.accrual_on(grant) is entry:
            total += entry.yearly
    return total


def _usage(
    plans: Sequence[YearPlan],
    buckets: Mapping[str, Bucket],
    calendar: WorkCalendar,
    start: dt.date,
    end: dt.date,
) -> dict[tuple[str, dt.date], _Use]:
    """Sum the leave and closure use of each bucket by month, from `start` to the `end` month."""
    use: defaultdict[tuple[str, dt.date], _Use] = defaultdict(_Use)
    for leave in (leave for plan in plans for leave in plan.leaves):
        bucket = buckets[leave.bucket]
        for day in leave.days():
            amount = _leave_use(leave, bucket, calendar.day(day))
            if amount and start <= day.replace(day=1) <= end:
                entry = (
                    _Use(taken=amount) if leave.state is LeaveState.TAKEN else _Use(planned=amount)
                )
                use[leave.bucket, day.replace(day=1)] += entry
    for closed in calendar.days(start, _month_end(end)):
        if closed.closure is not None:
            unit = buckets[closed.closure.bucket].unit
            amount = closed.hours if unit is Unit.HOURS else Decimal(1)
            use[closed.closure.bucket, closed.date.replace(day=1)] += _Use(closures=amount)
    return use


def _leave_use(leave: Leave, bucket: Bucket, day: Day) -> Decimal:
    """Return what `leave` uses of `bucket` on `day`, in the bucket's unit."""
    if bucket.unit is Unit.CALENDAR_DAYS:
        return Decimal(1)
    if day.kind is not DayKind.WORKING:
        return _ZERO
    hours = day.hours if leave.hours is None else leave.hours
    if bucket.unit is Unit.HOURS:
        return hours
    return (hours / day.hours).quantize(_CENT, ROUND_HALF_UP)


def _leave_notices(
    plans: Sequence[YearPlan],
    buckets: Mapping[str, Bucket],
    calendar: WorkCalendar,
    today: dt.date,
) -> Iterator[Notice]:
    for leave in (leave for plan in plans for leave in plan.leaves):
        bucket = buckets[leave.bucket]
        if leave.state is LeaveState.PLANNED and leave.last < today:
            message = f"{leave} is still planned; mark it taken or remove it"
            yield Notice(NoticeKind.PAST_PLANNED, leave.first, message, leave.bucket)
        if not any(_leave_use(leave, bucket, calendar.day(day)) for day in leave.days()):
            message = f"{leave} has no working day, so it uses no {leave.bucket}"
            yield Notice(NoticeKind.NO_LEAVE_USED, leave.first, message, leave.bucket)


def _credit(lots: defaultdict[int, Decimal], year: int, amount: Decimal) -> None:
    """Add `amount` to the lot of `year`; a negative amount is used oldest lot first."""
    if amount >= 0:
        lots[year] += amount
    else:
        _debit(lots, -amount, year)


def _debit(lots: defaultdict[int, Decimal], amount: Decimal, year: int) -> None:
    """Use `amount` from the oldest lots that have some left; what remains overdraws `year`."""
    for lot in sorted(lots):
        if amount <= 0:
            return
        taken = min(lots[lot], amount)
        if taken > 0:
            lots[lot] -= taken
            amount -= taken
    if amount > 0:
        lots[year] -= amount


def _months(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    month = start
    while month <= end:
        yield month
        month = _month_end(month) + dt.timedelta(days=1)


def _month_end(month: dt.date) -> dt.date:
    following = month.replace(day=28) + dt.timedelta(days=4)
    return following - dt.timedelta(days=following.day)
