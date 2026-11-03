import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from vacation_calendar.holiday_set import CountryBase, HolidayAddition, HolidayRemoval, MarketBase
from vacation_calendar.schedule import ScheduleEntry, WeekHours
from vacation_calendar.work_calendar import (
    CalendarConfig,
    Closure,
    DayKind,
    ExtraWorkday,
    WorkCalendar,
)

FULL_TIME = WeekHours(
    mon=Decimal(8), tue=Decimal(8), wed=Decimal(8), thu=Decimal(8), fri=Decimal(8)
)
SINCE_2020 = (ScheduleEntry(valid_from=dt.date(2020, 1, 1), hours=FULL_TIME),)


def calendar(**fields: object) -> WorkCalendar:
    return WorkCalendar(CalendarConfig.model_validate({"schedule": SINCE_2020, **fields}))


def kind(cal: WorkCalendar, day: dt.date) -> DayKind:
    return cal.day(day).kind


def test_italy_bologna_2026_holidays() -> None:
    cal = calendar(base=CountryBase(country="IT", subdiv="BO"))
    assert list(cal.holidays(2026)) == [
        dt.date(2026, 1, 1),
        dt.date(2026, 1, 6),
        dt.date(2026, 4, 5),  # Easter
        dt.date(2026, 4, 6),  # Easter Monday
        dt.date(2026, 4, 25),
        dt.date(2026, 5, 1),
        dt.date(2026, 6, 2),
        dt.date(2026, 8, 15),
        dt.date(2026, 10, 4),  # Saint Francis (national) and Saint Petronius (Bologna)
        dt.date(2026, 11, 1),
        dt.date(2026, 12, 8),
        dt.date(2026, 12, 25),
        dt.date(2026, 12, 26),
    ]


def test_removal_by_name_frees_the_day() -> None:
    base = CountryBase(country="IT")
    friday = dt.date(2025, 12, 26)
    assert kind(calendar(base=base), friday) is DayKind.HOLIDAY
    cal = calendar(base=base, remove=[HolidayRemoval(name="Santo Stefano")])
    assert cal.day(friday).kind is DayKind.WORKING
    assert cal.day(friday).hours == Decimal(8)


def test_removal_by_name_keeps_other_names_on_the_same_day() -> None:
    cal = calendar(
        base=CountryBase(country="IT", subdiv="BO", language="en_US"),
        remove=[HolidayRemoval(name="Saint Petronius's Day")],
    )
    assert cal.holidays(2026)[dt.date(2026, 10, 4)] == (
        "Saint Francis of Assisi, Patron Saint of Italy",
    )


@pytest.fixture
def english_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.setenv(variable, "en_US.UTF-8")


@pytest.fixture
def italian_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in ("LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.setenv(variable, "it_IT.UTF-8")


@pytest.mark.usefixtures("english_locale")
def test_country_names_default_to_its_language_not_the_locale() -> None:
    cal = calendar(base=CountryBase(country="IT"))
    assert cal.holidays(2026)[dt.date(2026, 12, 26)] == ("Santo Stefano",)


def test_market_names_default_to_its_language_not_the_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LANGUAGE", "hi")  # a language XNYS supports, unlike Italian
    cal = calendar(base=MarketBase(market="XNYS"))
    assert cal.holidays(2026)[dt.date(2026, 4, 3)] == ("Good Friday",)


@pytest.mark.usefixtures("italian_locale")
def test_explicit_language() -> None:
    cal = calendar(base=CountryBase(country="DE", subdiv="BY", language="en_US"))
    assert cal.holidays(2026)[dt.date(2026, 1, 6)] == ("Epiphany",)


def test_removal_by_date_respects_validity() -> None:
    cal = calendar(
        base=CountryBase(country="IT"),
        remove=[HolidayRemoval(date=dt.date(2026, 6, 2), valid_until=dt.date(2025, 12, 31))],
    )
    assert kind(cal, dt.date(2026, 6, 2)) is DayKind.HOLIDAY


def test_company_holiday_by_rule_from_a_date() -> None:
    christmas_eve = HolidayAddition(
        name="Christmas Eve",
        rule="FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=24",
        valid_from=dt.date(2026, 1, 1),
    )
    cal = calendar(base=CountryBase(country="IT"), add=[christmas_eve])
    assert kind(cal, dt.date(2025, 12, 24)) is DayKind.WORKING
    assert cal.day(dt.date(2026, 12, 24)).names == ("Christmas Eve",)


def test_schedule_change_mid_year() -> None:
    short_friday = FULL_TIME.model_copy(update={"fri": Decimal(4)})
    schedule = (*SINCE_2020, ScheduleEntry(valid_from=dt.date(2026, 7, 1), hours=short_friday))
    cal = WorkCalendar(CalendarConfig(schedule=schedule))
    assert cal.day(dt.date(2026, 6, 26)).hours == Decimal(8)
    assert cal.day(dt.date(2026, 7, 3)).hours == Decimal(4)


def test_day_before_the_first_schedule_is_an_error() -> None:
    with pytest.raises(ValueError, match="no work schedule before 2020-01-01"):
        calendar().day(dt.date(2019, 12, 31))


def test_days_span_years() -> None:
    cal = calendar(base=CountryBase(country="IT"))
    kinds = [day.kind for day in cal.days(dt.date(2025, 12, 31), dt.date(2026, 1, 2))]
    assert kinds == [DayKind.WORKING, DayKind.HOLIDAY, DayKind.WORKING]


def test_closure_uses_hours_and_a_holiday_wins_over_it() -> None:
    shutdown = Closure(
        name="Summer shutdown",
        bucket="vacation",
        start=dt.date(2025, 8, 11),
        end=dt.date(2025, 8, 15),
    )
    cal = calendar(base=CountryBase(country="IT"), closures=[shutdown])
    monday = cal.day(dt.date(2025, 8, 11))
    assert (monday.kind, monday.hours, monday.closure) == (DayKind.CLOSURE, Decimal(8), shutdown)
    assert kind(cal, dt.date(2025, 8, 15)) is DayKind.HOLIDAY
    assert list(cal.working_days(dt.date(2025, 8, 11), dt.date(2025, 8, 15))) == []


def test_overlapping_closures_are_an_error() -> None:
    first = Closure(name="A", bucket="vacation", date=dt.date(2026, 8, 10))
    second = Closure(name="B", bucket="vacation", date=dt.date(2026, 8, 10))
    with pytest.raises(ValueError, match="overlap on 2026-08-10"):
        calendar(closures=[first, second]).day(dt.date(2026, 8, 10))


def test_extra_workday_on_a_saturday() -> None:
    saturday = dt.date(2026, 3, 14)
    cal = calendar(extra_workdays=[ExtraWorkday(date=saturday, hours=Decimal(4))])
    assert (cal.day(saturday).kind, cal.day(saturday).hours) == (DayKind.WORKING, Decimal(4))


def test_china_make_up_working_day_from_the_base() -> None:
    sunday = dt.date(2026, 1, 4)
    cal = calendar(base=CountryBase(country="CN"))
    assert (cal.day(sunday).kind, cal.day(sunday).hours) == (DayKind.WORKING, Decimal(8))


def test_germany_holidays_depend_on_the_state() -> None:
    epiphany = dt.date(2026, 1, 6)
    assert kind(calendar(base=CountryBase(country="DE", subdiv="BY")), epiphany) is DayKind.HOLIDAY
    assert kind(calendar(base=CountryBase(country="DE", subdiv="BE")), epiphany) is DayKind.WORKING


def test_united_states_observed_independence_day() -> None:
    cal = calendar(base=CountryBase(country="US"))
    assert kind(cal, dt.date(2026, 7, 3)) is DayKind.HOLIDAY  # 4 July 2026 is a Saturday


def test_england_substitute_boxing_day() -> None:
    cal = calendar(base=CountryBase(country="GB", subdiv="ENG"))
    assert kind(cal, dt.date(2026, 12, 28)) is DayKind.HOLIDAY  # 26 December 2026 is a Saturday


def test_japan_substitute_holiday() -> None:
    cal = calendar(base=CountryBase(country="JP"))
    assert kind(cal, dt.date(2026, 5, 6)) is DayKind.HOLIDAY  # 3 May 2026 is a Sunday


def test_israel_weekend_from_the_schedule() -> None:
    sun_to_thu = WeekHours(
        sun=Decimal(8), mon=Decimal(8), tue=Decimal(8), wed=Decimal(8), thu=Decimal(8)
    )
    schedule = (ScheduleEntry(valid_from=dt.date(2020, 1, 1), hours=sun_to_thu),)
    cal = WorkCalendar(CalendarConfig(schedule=schedule, base=CountryBase(country="IL")))
    assert kind(cal, dt.date(2026, 3, 13)) is DayKind.REST  # Friday
    assert kind(cal, dt.date(2026, 3, 15)) is DayKind.WORKING  # Sunday


def test_market_base() -> None:
    cal = calendar(base=MarketBase(market="XNYS"))
    assert kind(cal, dt.date(2026, 4, 3)) is DayKind.HOLIDAY  # Good Friday


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"base": {"country": "XX"}}, "country 'XX' is not supported"),
        ({"base": {"country": "IT", "subdiv": "ZZ"}}, "subdivision 'ZZ' is not supported"),
        ({"base": {"market": "NOPE"}}, "market 'NOPE' is not supported"),
        (
            {"base": {"country": "IT", "language": "it"}},
            "language 'it' is not supported for IT; supported: en_US, it_IT, th",
        ),
        ({"base": {"market": "XNYS", "language": "it_IT"}}, "not supported for XNYS"),
        ({"schedule": [*SINCE_2020, *SINCE_2020]}, "start on the same day"),
        ({"schedule": []}, "at least 1 item"),
        ({"remove": [{"date": dt.date(2026, 1, 1), "name": "New Year"}]}, "exactly one of"),
    ],
)
def test_invalid_config_is_rejected(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        CalendarConfig.model_validate({"schedule": SINCE_2020, **fields})


def test_schedule_without_working_hours_is_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one day with working hours"):
        WeekHours()


def test_usual_day_is_the_most_common_hours() -> None:
    assert FULL_TIME.model_copy(update={"fri": Decimal(4)}).usual_day() == Decimal(8)
