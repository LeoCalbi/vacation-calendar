"""Public holidays: a base set from the `holidays` library, plus additions and removals."""

import datetime as dt
from collections import defaultdict
from typing import Self

import holidays
from pydantic import model_validator

from vacation_calendar.dates import DateSpec, Frozen, Validity


class CountryBase(Frozen):
    """Public holidays of a country, optionally of one subdivision (state, region, province)."""

    country: str
    subdiv: str | None = None
    language: str | None = None

    @model_validator(mode="after")
    def _check_supported(self) -> Self:
        supported = holidays.list_supported_countries()
        if self.country not in supported:
            msg = f"country {self.country!r} is not supported by the holidays library"
            raise ValueError(msg)
        if self.subdiv is not None and self.subdiv not in supported[self.country]:
            msg = f"subdivision {self.subdiv!r} is not supported for {self.country}"
            raise ValueError(msg)
        return self

    def load(self, year: int) -> holidays.HolidayBase:
        """Return the library's holidays for `year`."""
        return holidays.country_holidays(
            self.country, subdiv=self.subdiv, years=year, language=self.language
        )


class MarketBase(Frozen):
    """Holidays of a financial market, such as `XNYS` (New York Stock Exchange)."""

    market: str

    @model_validator(mode="after")
    def _check_supported(self) -> Self:
        if self.market not in holidays.list_supported_financial():
            msg = f"market {self.market!r} is not supported by the holidays library"
            raise ValueError(msg)
        return self

    def load(self, year: int) -> holidays.HolidayBase:
        """Return the library's holidays for `year`."""
        return holidays.financial_holidays(self.market, years=year)


class HolidayAddition(DateSpec):
    """A holiday the base set lacks, such as a company holiday; it uses no leave."""

    name: str


class HolidayRemoval(Validity):
    """Removes base holidays, either every holiday on a `date` or one holiday by `name`."""

    date: dt.date | None = None
    name: str | None = None

    @model_validator(mode="after")
    def _check_form(self) -> Self:
        if (self.date is None) == (self.name is None):
            msg = "give exactly one of: date, name"
            raise ValueError(msg)
        return self

    def removes(self, day: dt.date, name: str) -> bool:
        """Return whether this removal drops the holiday `name` on `day`."""
        if not self.applies_on(day):
            return False
        return day == self.date if self.date is not None else name == self.name


def holiday_names(
    base: CountryBase | MarketBase | None,
    additions: tuple[HolidayAddition, ...],
    removals: tuple[HolidayRemoval, ...],
    year: int,
) -> dict[dt.date, tuple[str, ...]]:
    """Return the holidays of `year` and their names: the base minus removals, plus additions."""
    names: defaultdict[dt.date, list[str]] = defaultdict(list)
    if base is not None:
        loaded = base.load(year)
        for day in sorted(loaded):
            names[day].extend(
                name
                for name in loaded.get_list(day)
                if not any(removal.removes(day, name) for removal in removals)
            )
    start, end = dt.date(year, 1, 1), dt.date(year, 12, 31)
    for addition in additions:
        for day in addition.occurrences(start, end):
            names[day].append(addition.name)
    return {day: tuple(found) for day, found in sorted(names.items()) if found}


def base_weekend_workdays(base: CountryBase | MarketBase | None, year: int) -> set[dt.date]:
    """Return the weekend days the base declares as working days (make-up days, as in China)."""
    if base is None:
        return set()
    return set(base.load(year).weekend_workdays)
