"""Working hours per weekday, changing over time."""

import datetime as dt
from decimal import Decimal
from typing import Annotated, Self

from pydantic import Field, model_validator

from vacation_calendar.dates import Frozen

Hours = Annotated[Decimal, Field(ge=0, le=24, decimal_places=2)]
"""A number of hours with at most two decimals, as payslips print them."""


class WeekHours(Frozen):
    """Scheduled working hours for each weekday; a day with 0 hours is a rest day."""

    mon: Hours = Decimal(0)
    tue: Hours = Decimal(0)
    wed: Hours = Decimal(0)
    thu: Hours = Decimal(0)
    fri: Hours = Decimal(0)
    sat: Hours = Decimal(0)
    sun: Hours = Decimal(0)

    @model_validator(mode="after")
    def _check_some_work(self) -> Self:
        if not any(self.by_weekday()):
            msg = "a schedule needs at least one day with working hours"
            raise ValueError(msg)
        return self

    def by_weekday(self) -> tuple[Decimal, ...]:
        """Return the hours from Monday (index 0) to Sunday (index 6), like `date.weekday()`."""
        return (self.mon, self.tue, self.wed, self.thu, self.fri, self.sat, self.sun)

    def on(self, day: dt.date) -> Decimal:
        """Return the scheduled hours on the weekday of `day`."""
        return self.by_weekday()[day.weekday()]

    def usual_day(self) -> Decimal:
        """Return the most common non-zero daily hours (the longest on a tie)."""
        working = [hours for hours in self.by_weekday() if hours]
        return max(working, key=lambda hours: (working.count(hours), hours))


class ScheduleEntry(Frozen):
    """A weekly schedule that applies from `valid_from` until the next entry."""

    valid_from: dt.date
    hours: WeekHours
