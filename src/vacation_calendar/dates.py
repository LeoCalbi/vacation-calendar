"""Dates that configuration entries apply to: single days, ranges, rules and validity windows."""

import datetime as dt
from collections.abc import Iterator, Sequence
from functools import cached_property
from typing import Protocol, Self

from dateutil.rrule import rrule, rrulestr
from pydantic import BaseModel, ConfigDict, model_validator

_RULE_EPOCH = dt.date(1900, 1, 1)
"""Start of a recurrence rule that has no `valid_from`."""


class Frozen(BaseModel):
    """Base for immutable configuration models that reject unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid")


class Validity(Frozen):
    """An optional window of dates, both ends inclusive, in which an entry applies."""

    valid_from: dt.date | None = None
    valid_until: dt.date | None = None

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            msg = f"valid_until {self.valid_until} is before valid_from {self.valid_from}"
            raise ValueError(msg)
        return self

    def applies_on(self, day: dt.date) -> bool:
        """Return whether the entry applies on `day`."""
        if self.valid_from and day < self.valid_from:
            return False
        return not (self.valid_until and day > self.valid_until)


class DateSpec(Validity):
    """The days an entry falls on: one `date`, a `start`-`end` range, or an RFC 5545 `rule`.

    A rule is an iCalendar RRULE such as `FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=24`, with
    dateutil's `BYEASTER` extension for days relative to Easter (`FREQ=YEARLY;BYEASTER=1`).
    Rules repeat from `valid_from`, so interval rules (every second Friday) need one.
    """

    date: dt.date | None = None
    start: dt.date | None = None
    end: dt.date | None = None
    rule: str | None = None

    @model_validator(mode="after")
    def _check_form(self) -> Self:
        forms = [
            self.date is not None,
            self.start is not None or self.end is not None,
            bool(self.rule),
        ]
        if sum(forms) != 1:
            msg = "give exactly one of: date, start and end, rule"
            raise ValueError(msg)
        if (self.start is None) != (self.end is None):
            msg = "a range needs both start and end"
            raise ValueError(msg)
        if self.start and self.end and self.end < self.start:
            msg = f"end {self.end} is before start {self.start}"
            raise ValueError(msg)
        if self.rule:
            _ = self._rrule  # parse now, so a bad rule fails when the config loads
        return self

    @cached_property
    def _rrule(self) -> rrule:
        start = dt.datetime.combine(self.valid_from or _RULE_EPOCH, dt.time())
        try:
            parsed = rrulestr(self.rule or "", dtstart=start)
        except ValueError as error:
            msg = f"invalid rule {self.rule!r}: {error}"
            raise ValueError(msg) from error
        if not isinstance(parsed, rrule):
            msg = f"rule {self.rule!r} must be a single RRULE"
            raise TypeError(msg)
        return parsed

    def occurrences(self, start: dt.date, end: dt.date) -> Iterator[dt.date]:
        """Yield the days in `start`-`end` (inclusive) the entry falls on and applies to."""
        if self.date is not None:
            days: Iterator[dt.date] = iter([self.date])
        elif self.start is not None and self.end is not None:
            days = _date_range(max(start, self.start), min(end, self.end))
        else:
            after = dt.datetime.combine(start, dt.time())
            before = dt.datetime.combine(end, dt.time())
            days = (moment.date() for moment in self._rrule.between(after, before, inc=True))
        yield from (day for day in days if start <= day <= end and self.applies_on(day))


class HasValidFrom(Protocol):
    """An entry that takes effect on a date and lasts until the next entry."""

    @property
    def valid_from(self) -> dt.date:
        """First day the entry applies."""
        ...


def effective[T: HasValidFrom](entries: Sequence[T], day: dt.date) -> T | None:
    """Return the entry in effect on `day`: the latest one starting on or before it."""
    current = [entry for entry in entries if entry.valid_from <= day]
    return max(current, key=lambda entry: entry.valid_from, default=None)


def _date_range(start: dt.date, end: dt.date) -> Iterator[dt.date]:
    day = start
    while day <= end:
        yield day
        day += dt.timedelta(days=1)
