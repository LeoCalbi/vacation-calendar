import datetime as dt
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from vacation_calendar.dates import DateSpec, effective

YEAR_2026 = (dt.date(2026, 1, 1), dt.date(2026, 12, 31))
TWO_YEARS = (dt.date(2026, 1, 1), dt.date(2027, 12, 31))


@dataclass(frozen=True)
class Entry:
    valid_from: dt.date


def occurrences(spec: DateSpec, window: tuple[dt.date, dt.date]) -> list[dt.date]:
    return list(spec.occurrences(*window))


@pytest.mark.parametrize(
    ("rule", "expected"),
    [
        ("FREQ=YEARLY;BYMONTH=8;BYMONTHDAY=14", [dt.date(2026, 8, 14), dt.date(2027, 8, 14)]),
        ("FREQ=YEARLY;BYEASTER=1", [dt.date(2026, 4, 6), dt.date(2027, 3, 29)]),
        ("FREQ=YEARLY;BYMONTH=7;BYDAY=-1FR", [dt.date(2026, 7, 31), dt.date(2027, 7, 30)]),
        # the day after US Thanksgiving: the Friday from the 23rd to the 29th of November
        (
            "FREQ=YEARLY;BYMONTH=11;BYDAY=FR;BYMONTHDAY=23,24,25,26,27,28,29",
            [dt.date(2026, 11, 27), dt.date(2027, 11, 26)],
        ),
    ],
)
def test_rule_occurrences(rule: str, expected: list[dt.date]) -> None:
    assert occurrences(DateSpec(rule=rule), TWO_YEARS) == expected


def test_single_date_outside_window_yields_nothing() -> None:
    assert occurrences(DateSpec(date=dt.date(2025, 12, 24)), YEAR_2026) == []


def test_range_is_clipped_to_window() -> None:
    spec = DateSpec(start=dt.date(2025, 12, 30), end=dt.date(2026, 1, 2))
    assert occurrences(spec, YEAR_2026) == [dt.date(2026, 1, 1), dt.date(2026, 1, 2)]


def test_validity_window_limits_a_rule() -> None:
    spec = DateSpec(
        rule="FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=24",
        valid_from=dt.date(2026, 1, 1),
        valid_until=dt.date(2026, 12, 31),
    )
    window = (dt.date(2025, 1, 1), dt.date(2027, 12, 31))
    assert occurrences(spec, window) == [dt.date(2026, 12, 24)]


def test_interval_rule_counts_from_valid_from() -> None:
    spec = DateSpec(rule="FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", valid_from=dt.date(2026, 1, 9))
    window = (dt.date(2026, 1, 1), dt.date(2026, 1, 31))
    assert occurrences(spec, window) == [dt.date(2026, 1, 9), dt.date(2026, 1, 23)]


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({}, "exactly one of"),
        ({"date": dt.date(2026, 1, 1), "rule": "FREQ=YEARLY"}, "exactly one of"),
        ({"start": dt.date(2026, 1, 1)}, "both start and end"),
        ({"start": dt.date(2026, 1, 2), "end": dt.date(2026, 1, 1)}, "is before start"),
        ({"rule": "FREQ=SOMETIMES"}, "invalid rule"),
        (
            {
                "date": dt.date(2026, 1, 1),
                "valid_from": dt.date(2026, 2, 1),
                "valid_until": dt.date(2026, 1, 1),
            },
            "is before valid_from",
        ),
        ({"date": dt.date(2026, 1, 1), "colour": "red"}, "Extra inputs are not permitted"),
    ],
)
def test_invalid_specs_are_rejected(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        DateSpec.model_validate(fields)


def test_effective_picks_latest_entry_started() -> None:
    entries = [Entry(dt.date(2026, 1, 1)), Entry(dt.date(2026, 7, 1))]
    assert effective(entries, dt.date(2025, 12, 31)) is None
    assert effective(entries, dt.date(2026, 6, 30)) is entries[0]
    assert effective(entries, dt.date(2026, 7, 1)) is entries[1]
