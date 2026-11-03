"""Leave buckets and their accrual, leaves, payslip checkpoints, and the checks that tie them."""

import datetime as dt
import re
from collections import defaultdict
from collections.abc import Iterator, Mapping
from decimal import Decimal
from enum import StrEnum
from functools import cached_property
from typing import Annotated, Self

from pydantic import AfterValidator, BeforeValidator, Field, model_validator

from vacation_calendar.dates import Frozen, effective
from vacation_calendar.work_calendar import CalendarConfig, DayKind, WorkCalendar

Amount = Annotated[Decimal, Field(decimal_places=2)]
"""A balance in a bucket's unit, with at most two decimals; negative when overdrawn."""

Rate = Annotated[Decimal, Field(ge=0, decimal_places=2)]
"""An amount credited to a bucket, in the bucket's unit."""

LeaveHours = Annotated[Decimal, Field(gt=0, le=24, decimal_places=2)]
"""The hours of leave on one day."""

_MONTH = re.compile(r"\d{4}-\d{2}")


def _parse_month(value: object) -> object:
    if isinstance(value, str) and _MONTH.fullmatch(value):
        return dt.date(int(value[:4]), int(value[5:]), 1)
    return value


Month = Annotated[
    dt.date, BeforeValidator(_parse_month), AfterValidator(lambda day: day.replace(day=1))
]
"""A calendar month, written `"2026-09"` or as any date in it; stored as its first day."""


class Unit(StrEnum):
    """What a bucket counts: hours, working days, or calendar days (weekends included)."""

    HOURS = "hours"
    DAYS = "days"
    CALENDAR_DAYS = "calendar_days"


class Accrual(Frozen):
    """How a bucket grows from `valid_from` until the next entry.

    `monthly` is credited for each month from `valid_from`, which must be a month's first day.
    `yearly` is granted at once every year on the month and day of `valid_from`.
    """

    valid_from: dt.date
    monthly: Rate | None = None
    yearly: Rate | None = None

    @model_validator(mode="after")
    def _check_form(self) -> Self:
        if (self.monthly is None) == (self.yearly is None):
            msg = "give exactly one of: monthly, yearly"
            raise ValueError(msg)
        if self.monthly is not None and self.valid_from.day != 1:
            msg = f"a monthly accrual starts on a month's first day, not {self.valid_from}"
            raise ValueError(msg)
        if self.yearly is not None and (self.valid_from.month, self.valid_from.day) == (2, 29):
            msg = "a yearly grant cannot fall on 29 February; use 28 February or 1 March"
            raise ValueError(msg)
        return self


class Bucket(Frozen):
    """A kind of leave with its own balance, such as vacation or reduced working hours."""

    name: str
    unit: Unit = Unit.HOURS
    accrual: tuple[Accrual, ...] = ()

    @model_validator(mode="after")
    def _check_accrual_dates(self) -> Self:
        starts = [entry.valid_from for entry in self.accrual]
        if len(set(starts)) != len(starts):
            msg = "two accrual entries start on the same day"
            raise ValueError(msg)
        return self

    def accrual_on(self, day: dt.date) -> Accrual | None:
        """Return the accrual in effect on `day`, or None before the first one."""
        return effective(self.accrual, day)


class Config(Frozen):
    """The settings shared by every year: the work calendar and the buckets, by key."""

    calendar: CalendarConfig
    buckets: dict[str, Bucket] = Field(min_length=1)

    @model_validator(mode="after")
    def _check_closure_buckets(self) -> Self:
        for closure in self.calendar.closures:
            if closure.bucket not in self.buckets:
                msg = f"closure {closure.name!r}: {_unknown(closure.bucket, self.buckets)}"
                raise ValueError(msg)
        return self


class LeaveState(StrEnum):
    """Whether a leave is only planned or already taken."""

    PLANNED = "planned"
    TAKEN = "taken"


class Leave(Frozen):
    """Time off from one bucket on one `date` or on a `start`-`end` range.

    Without `hours`, each working day in it is taken in full; with `hours`, that many hours per
    working day. Rest days, holidays and closure days inside a range use no leave.
    """

    date: dt.date | None = None
    start: dt.date | None = None
    end: dt.date | None = None
    hours: LeaveHours | None = None
    bucket: str
    state: LeaveState = LeaveState.PLANNED

    @model_validator(mode="after")
    def _check_form(self) -> Self:
        if (self.date is None) == (self.start is None and self.end is None):
            msg = "give exactly one of: date, start and end"
            raise ValueError(msg)
        if (self.start is None) != (self.end is None):
            msg = "a range needs both start and end"
            raise ValueError(msg)
        if self.start and self.end and self.end < self.start:
            msg = f"end {self.end} is before start {self.start}"
            raise ValueError(msg)
        return self

    @property
    def first(self) -> dt.date:
        """Return the first day of the leave."""
        return self._span[0]

    @property
    def last(self) -> dt.date:
        """Return the last day of the leave."""
        return self._span[1]

    @cached_property
    def _span(self) -> tuple[dt.date, dt.date]:
        if self.date is not None:
            return self.date, self.date
        if self.start is None or self.end is None:  # ruled out by the form check
            msg = "a leave needs a date, or a start and an end"
            raise ValueError(msg)
        return self.start, self.end

    def days(self) -> Iterator[dt.date]:
        """Yield every calendar day from the first to the last day of the leave."""
        for offset in range((self.last - self.first).days + 1):
            yield self.first + dt.timedelta(days=offset)

    def __str__(self) -> str:
        """Return the leave's dates, as error messages name it."""
        if self.date is not None:
            return f"leave on {self.date}"
        return f"leave {self.first}..{self.last}"


class PayslipMode(StrEnum):
    """What a payslip balance does to the computed one."""

    CHECK = "check"
    """Report the difference, keep computing from the formula."""
    OVERRIDE = "override"
    """Replace the computed balance from that month on."""


class PayslipEntry(Frozen):
    """The balances a payslip prints at the end of `month`, by bucket key."""

    month: Month
    mode: PayslipMode
    balances: dict[str, Amount] = Field(min_length=1)


class YearPlan(Frozen):
    """One year of leave data: an optional carry-over, the leaves and the payslip checkpoints.

    `carry_over` is each bucket's balance on 1 January; a bucket left out is computed from the
    previous year.
    """

    year: int
    carry_over: dict[str, Amount] = Field(default_factory=dict)
    leaves: tuple[Leave, ...] = ()
    payslips: tuple[PayslipEntry, ...] = ()

    @model_validator(mode="after")
    def _check_inside_year(self) -> Self:
        for leave in self.leaves:
            if leave.first.year != self.year or leave.last.year != self.year:
                msg = f"{leave} is not inside {self.year}; split it at the end of the year"
                raise ValueError(msg)
        months = [payslip.month for payslip in self.payslips]
        for month in months:
            if month.year != self.year:
                msg = f"payslip {month:%Y-%m} is not in {self.year}"
                raise ValueError(msg)
            if months.count(month) > 1:
                msg = f"two payslips for {month:%Y-%m}"
                raise ValueError(msg)
        return self


class PlanError(ValueError):
    """A year plan that does not fit the configuration; `problems` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        """Create the error from one message per problem."""
        self.problems = tuple(problems)
        super().__init__("\n".join(problems))


def check_year(plan: YearPlan, buckets: Mapping[str, Bucket], calendar: WorkCalendar) -> None:
    """Raise `PlanError` with every problem in `plan`, or return if it fits the configuration.

    Problems: a bucket that is not configured, hours on a bucket that counts calendar days, a
    leave outside the work schedule, and leaves on one day that total more than its hours.
    """
    problems = [
        f"{where}: {_unknown(key, buckets)}"
        for where, key in _bucket_references(plan)
        if key not in buckets
    ]
    booked: defaultdict[dt.date, Decimal] = defaultdict(Decimal)
    for leave in plan.leaves:
        bucket = buckets.get(leave.bucket)
        if bucket is not None and bucket.unit is Unit.CALENDAR_DAYS and leave.hours is not None:
            problems.append(f"{leave}: bucket {leave.bucket!r} counts whole calendar days")
        try:
            days = [calendar.day(day) for day in leave.days()]
        except ValueError as error:
            problems.append(f"{leave}: {error}")
            continue
        for day in days:
            if day.kind is DayKind.WORKING:
                booked[day.date] += day.hours if leave.hours is None else leave.hours
    for date, hours in sorted(booked.items()):
        scheduled = calendar.day(date).hours
        if hours > scheduled:
            problems.append(f"{date}: leaves total {hours} h, but the schedule has {scheduled} h")
    if problems:
        raise PlanError(problems)


def _bucket_references(plan: YearPlan) -> Iterator[tuple[str, str]]:
    for key in plan.carry_over:
        yield "carry_over", key
    for leave in plan.leaves:
        yield str(leave), leave.bucket
    for payslip in plan.payslips:
        for key in payslip.balances:
            yield f"payslip {payslip.month:%Y-%m}", key


def _unknown(key: str, buckets: Mapping[str, Bucket]) -> str:
    return f"unknown bucket {key!r}; configured: {', '.join(sorted(buckets)) or 'none'}"
