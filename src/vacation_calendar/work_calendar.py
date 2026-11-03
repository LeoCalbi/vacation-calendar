"""The work calendar: what kind of day each date is, and how many hours it counts."""

import datetime as dt
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from vacation_calendar.dates import DateSpec, Frozen, effective
from vacation_calendar.holiday_set import (
    CountryBase,
    HolidayAddition,
    HolidayRemoval,
    MarketBase,
    base_weekend_workdays,
    holiday_names,
)
from vacation_calendar.schedule import Hours, ScheduleEntry


class Closure(DateSpec):
    """Days the workplace is closed and the hours come from a leave bucket, like a summer shutdown.

    Unlike a holiday addition, a closure uses leave. A holiday on the same day wins.
    """

    name: str
    bucket: str


class ExtraWorkday(DateSpec):
    """A rest day that becomes a working day, for `hours` or the schedule's usual day."""

    name: str | None = None
    hours: Hours | None = None


class CalendarConfig(Frozen):
    """Everything that decides the working days and their hours."""

    schedule: tuple[ScheduleEntry, ...] = Field(min_length=1)
    base: CountryBase | MarketBase | None = None
    add: tuple[HolidayAddition, ...] = ()
    remove: tuple[HolidayRemoval, ...] = ()
    closures: tuple[Closure, ...] = ()
    extra_workdays: tuple[ExtraWorkday, ...] = ()

    @model_validator(mode="after")
    def _check_schedule_dates(self) -> Self:
        starts = [entry.valid_from for entry in self.schedule]
        if len(set(starts)) != len(starts):
            msg = "two schedule entries start on the same day"
            raise ValueError(msg)
        return self


class DayKind(StrEnum):
    """What kind of day a date is for the person's work."""

    WORKING = "working"
    REST = "rest"
    HOLIDAY = "holiday"
    CLOSURE = "closure"


@dataclass(frozen=True, slots=True)
class Day:
    """One date in the work calendar.

    `hours` are the scheduled working hours: non-zero for working and closure days, zero otherwise.
    `names` holds the holiday names, or the closure name.
    """

    date: dt.date
    kind: DayKind
    hours: Decimal
    names: tuple[str, ...] = ()
    closure: Closure | None = None


@dataclass(frozen=True, slots=True)
class _Year:
    holidays: dict[dt.date, tuple[str, ...]]
    closures: dict[dt.date, Closure]
    extra_workdays: dict[dt.date, Hours | None]


class WorkCalendar:
    """Answers, for any date, whether it is a working day and how many hours it counts."""

    def __init__(self, config: CalendarConfig) -> None:
        """Create a calendar for `config`; years are computed on first use."""
        self.config = config
        self._years: dict[int, _Year] = {}

    def day(self, day: dt.date) -> Day:
        """Return what `day` is: its kind, scheduled hours and names."""
        year = self._year(day.year)
        names = year.holidays.get(day, ())
        if names:
            return Day(day, DayKind.HOLIDAY, Decimal(0), names)
        hours = self._scheduled_hours(day, year)
        if not hours:
            return Day(day, DayKind.REST, Decimal(0))
        closure = year.closures.get(day)
        if closure is not None:
            return Day(day, DayKind.CLOSURE, hours, (closure.name,), closure)
        return Day(day, DayKind.WORKING, hours)

    def days(self, start: dt.date, end: dt.date) -> Iterator[Day]:
        """Yield every day from `start` to `end`, both inclusive."""
        day = start
        while day <= end:
            yield self.day(day)
            day += dt.timedelta(days=1)

    def working_days(self, start: dt.date, end: dt.date) -> Iterator[Day]:
        """Yield the days in `start`-`end` on which leave can be taken."""
        return (day for day in self.days(start, end) if day.kind is DayKind.WORKING)

    def holidays(self, year: int) -> dict[dt.date, tuple[str, ...]]:
        """Return the holidays of `year` with their names, after additions and removals."""
        return dict(self._year(year).holidays)

    def _scheduled_hours(self, day: dt.date, year: _Year) -> Decimal:
        entry = effective(self.config.schedule, day)
        if entry is None:
            first = min(item.valid_from for item in self.config.schedule)
            msg = f"no work schedule before {first}; {day} is outside it"
            raise ValueError(msg)
        hours = entry.hours.on(day)
        if not hours and day in year.extra_workdays:
            extra = year.extra_workdays[day]
            return extra if extra is not None else entry.hours.usual_day()
        return hours

    def _year(self, year: int) -> _Year:
        if year not in self._years:
            self._years[year] = self._build_year(year)
        return self._years[year]

    def _build_year(self, year: int) -> _Year:
        config = self.config
        start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
        closures: dict[dt.date, Closure] = {}
        for closure in config.closures:
            for day in closure.occurrences(start, end):
                if day in closures:
                    msg = f"closures {closures[day].name!r} and {closure.name!r} overlap on {day}"
                    raise ValueError(msg)
                closures[day] = closure
        extra: dict[dt.date, Hours | None] = dict.fromkeys(base_weekend_workdays(config.base, year))
        for workday in config.extra_workdays:
            extra.update(dict.fromkeys(workday.occurrences(start, end), workday.hours))
        return _Year(
            holidays=holiday_names(config.base, config.add, config.remove, year),
            closures=closures,
            extra_workdays=extra,
        )
