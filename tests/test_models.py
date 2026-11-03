import datetime as dt
from decimal import Decimal

import pytest
from pydantic import ValidationError

from vacation_calendar.holiday_set import CountryBase
from vacation_calendar.models import (
    Accrual,
    Bucket,
    Config,
    Leave,
    PayslipEntry,
    PlanError,
    Unit,
    YearPlan,
    check_plans,
)
from vacation_calendar.schedule import ScheduleEntry, WeekHours
from vacation_calendar.work_calendar import CalendarConfig, Closure, WorkCalendar

SHORT_FRIDAY = WeekHours(
    mon=Decimal(8), tue=Decimal(8), wed=Decimal(8), thu=Decimal(8), fri=Decimal(4)
)
CALENDAR = CalendarConfig(
    schedule=(ScheduleEntry(valid_from=dt.date(2020, 1, 1), hours=SHORT_FRIDAY),),
    base=CountryBase(country="IT"),
)
BUCKETS = {
    "vacation": Bucket(
        name="Vacation",
        accrual=(Accrual(valid_from=dt.date(2020, 1, 1), monthly=Decimal("13.33")),),
    ),
    "rol": Bucket(name="ROL"),
    "ferias": Bucket(name="Férias", unit=Unit.CALENDAR_DAYS),
}


def plan(year: int = 2026, **fields: object) -> YearPlan:
    return YearPlan.model_validate({"year": year, **fields})


def check(*plans: YearPlan) -> None:
    check_plans(plans, BUCKETS, WorkCalendar(CALENDAR))


def problems(*plans: YearPlan) -> tuple[str, ...]:
    with pytest.raises(PlanError) as error:
        check(*plans)
    return error.value.problems


def test_toml_floats_become_exact_decimals() -> None:
    accrual = Accrual.model_validate({"valid_from": dt.date(2026, 1, 1), "monthly": 8.67})
    assert accrual.monthly == Decimal("8.67")


def test_bucket_defaults_to_hours_and_picks_the_accrual_in_effect() -> None:
    raise_ = Accrual(valid_from=dt.date(2026, 7, 1), monthly=Decimal("11.33"))
    bucket = Bucket(name="ROL", accrual=(*BUCKETS["vacation"].accrual, raise_))
    assert bucket.unit is Unit.HOURS
    assert bucket.accrual_on(dt.date(2019, 12, 31)) is None
    assert bucket.accrual_on(dt.date(2026, 6, 30)) == BUCKETS["vacation"].accrual[0]
    assert bucket.accrual_on(dt.date(2026, 7, 1)) is raise_


def test_yearly_grant_on_a_hire_anniversary() -> None:
    grant = Accrual(valid_from=dt.date(2026, 4, 1), yearly=Decimal(10))
    assert (grant.monthly, grant.yearly) == (None, Decimal(10))


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"valid_from": dt.date(2026, 1, 1)}, "exactly one of: monthly, yearly"),
        (
            {"valid_from": dt.date(2026, 1, 1), "monthly": 1, "yearly": 12},
            "exactly one of: monthly, yearly",
        ),
        ({"valid_from": dt.date(2026, 1, 15), "monthly": 1}, "month's first day, not 2026-01-15"),
        ({"valid_from": dt.date(2028, 2, 29), "yearly": 20}, "cannot fall on 29 February"),
        ({"valid_from": dt.date(2026, 1, 1), "monthly": 104 / 12}, "no more than 2 decimal"),
        ({"valid_from": dt.date(2026, 1, 1), "monthly": -1}, "greater than or equal to 0"),
    ],
)
def test_invalid_accrual_is_rejected(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Accrual.model_validate(fields)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"name": "Leave", "unit": "weeks"}, "'hours', 'days' or 'calendar_days'"),
        (
            {
                "name": "Leave",
                "accrual": [
                    {"valid_from": dt.date(2026, 1, 1), "monthly": 1},
                    {"valid_from": dt.date(2026, 1, 1), "yearly": 12},
                ],
            },
            "start on the same day",
        ),
    ],
)
def test_invalid_bucket_is_rejected(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Bucket.model_validate(fields)


def test_config_rejects_a_closure_with_an_unknown_bucket() -> None:
    shutdown = Closure(name="Shutdown", bucket="holiday", date=dt.date(2026, 8, 14))
    calendar = CALENDAR.model_copy(update={"closures": (shutdown,)})
    message = "closure 'Shutdown': unknown bucket 'holiday'; configured: ferias, rol, vacation"
    with pytest.raises(ValidationError, match=message):
        Config(calendar=calendar, buckets=BUCKETS)


def test_leave_range_spans_every_calendar_day() -> None:
    leave = Leave(start=dt.date(2026, 8, 14), end=dt.date(2026, 8, 17), bucket="vacation")
    assert (leave.first, leave.last, len(list(leave.days()))) == (
        dt.date(2026, 8, 14),
        dt.date(2026, 8, 17),
        4,
    )
    assert str(leave) == "leave 2026-08-14..2026-08-17"


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({}, "exactly one of: date, start and end"),
        (
            {"date": dt.date(2026, 3, 6), "start": dt.date(2026, 3, 6), "end": dt.date(2026, 3, 6)},
            "exactly one of: date, start and end",
        ),
        ({"end": dt.date(2026, 3, 6)}, "needs both start and end"),
        ({"start": dt.date(2026, 3, 6), "end": dt.date(2026, 3, 5)}, "is before start"),
        ({"date": dt.date(2026, 3, 6), "hours": 0}, "greater than 0"),
        ({"date": dt.date(2026, 3, 6), "hours": 25}, "less than or equal to 24"),
        ({"date": dt.date(2026, 3, 6), "state": "approved"}, "'planned' or 'taken'"),
    ],
)
def test_invalid_leave_is_rejected(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Leave.model_validate({"bucket": "vacation", **fields})


@pytest.mark.parametrize("month", ["2026-09", dt.date(2026, 9, 30)])
def test_payslip_month_from_text_or_a_date(month: object) -> None:
    payslip = PayslipEntry.model_validate({"month": month, "mode": "check", "balances": {"rol": 1}})
    assert payslip.month == dt.date(2026, 9, 1)


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"month": "2026-13"}, "month must be in 1..12"),
        ({"month": "September"}, "valid date"),
        ({"mode": "replace"}, "'check' or 'override'"),
        ({"balances": {}}, "at least 1 item"),
        ({"balances": {"rol": "1.005"}}, "no more than 2 decimal"),
    ],
)
def test_invalid_payslip_is_rejected(fields: dict[str, object], message: str) -> None:
    valid = {"month": "2026-09", "mode": "check", "balances": {"rol": 1}}
    with pytest.raises(ValidationError, match=message):
        PayslipEntry.model_validate({**valid, **fields})


def test_negative_balances_are_allowed() -> None:
    plan = YearPlan(year=2026, carry_over={"vacation": Decimal("-4.5")})
    assert plan.carry_over == {"vacation": Decimal("-4.50")}


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        (
            {"leaves": [{"start": "2025-12-29", "end": "2026-01-02", "bucket": "vacation"}]},
            "leave 2025-12-29..2026-01-02 starts outside 2026; file it under 2025",
        ),
        (
            {"leaves": [{"start": "2026-12-28", "end": "2028-01-04", "bucket": "vacation"}]},
            "leave 2026-12-28..2028-01-04 ends after 2027",
        ),
        (
            {"payslips": [{"month": "2025-12", "mode": "check", "balances": {"rol": 1}}]},
            "payslip 2025-12 is not in 2026",
        ),
        (
            {
                "payslips": [
                    {"month": "2026-03", "mode": "check", "balances": {"rol": 1}},
                    {"month": "2026-03-31", "mode": "override", "balances": {"rol": 2}},
                ]
            },
            "two payslips for 2026-03",
        ),
    ],
)
def test_invalid_year_plan_is_rejected(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        YearPlan.model_validate({"year": 2026, **fields})


def test_a_plan_that_fits_passes() -> None:
    check(
        plan(
            carry_over={"vacation": 25.5, "rol": 20.3},
            leaves=[
                # Monday to Friday around Easter Monday: the holiday uses no leave
                {
                    "start": "2026-04-06",
                    "end": "2026-04-10",
                    "bucket": "vacation",
                    "state": "taken",
                },
                # half a day from each of two buckets on the same day
                {"date": "2026-03-04", "hours": 4, "bucket": "vacation"},
                {"date": "2026-03-04", "hours": 4, "bucket": "rol"},
                # four hours a day from Friday to Monday: the weekend uses no leave
                {"start": "2026-03-13", "end": "2026-03-16", "hours": 4, "bucket": "rol"},
                {"start": "2026-08-01", "end": "2026-08-30", "bucket": "ferias"},
            ],
            payslips=[{"month": "2026-09", "mode": "override", "balances": {"vacation": 61.2}}],
        )
    )


def test_a_leave_across_new_year_clashes_with_the_next_years_plan() -> None:
    christmas = {"start": "2026-12-28", "end": "2027-01-08", "bucket": "vacation"}
    epiphany_bridge = {"date": "2027-01-07", "hours": 4, "bucket": "rol"}
    check(plan(leaves=[christmas]), plan(2027))
    assert problems(plan(leaves=[christmas]), plan(2027, leaves=[epiphany_bridge])) == (
        "2027-01-07: leaves total 12 h, but the schedule has 8 h",
    )


def test_two_plans_for_one_year_are_an_error() -> None:
    assert problems(plan(), plan()) == ("two plans for 2026",)


def test_every_unknown_bucket_is_reported_together() -> None:
    assert problems(
        plan(
            carry_over={"holiday": 1},
            leaves=[{"date": "2026-03-04", "bucket": "pto"}],
            payslips=[{"month": "2026-09", "mode": "check", "balances": {"rol": 1, "perm": 2}}],
        )
    ) == (
        "carry_over 2026: unknown bucket 'holiday'; configured: ferias, rol, vacation",
        "leave on 2026-03-04: unknown bucket 'pto'; configured: ferias, rol, vacation",
        "payslip 2026-09: unknown bucket 'perm'; configured: ferias, rol, vacation",
    )


def test_hours_above_a_short_friday_are_an_error() -> None:
    assert problems(plan(leaves=[{"date": "2026-03-06", "hours": 6, "bucket": "rol"}])) == (
        "2026-03-06: leaves total 6 h, but the schedule has 4 h",
    )


def test_a_full_day_and_another_leave_on_the_same_day_are_an_error() -> None:
    assert problems(
        plan(
            leaves=[
                {"start": "2026-03-02", "end": "2026-03-06", "bucket": "vacation"},
                {"date": "2026-03-05", "hours": 2, "bucket": "rol"},
            ]
        )
    ) == ("2026-03-05: leaves total 10 h, but the schedule has 8 h",)


def test_hours_on_a_calendar_days_bucket_are_an_error() -> None:
    assert problems(plan(leaves=[{"date": "2026-03-04", "hours": 4, "bucket": "ferias"}])) == (
        "leave on 2026-03-04: bucket 'ferias' counts whole calendar days",
    )


def test_leave_before_the_work_schedule_is_an_error() -> None:
    late_start = CALENDAR.model_copy(
        update={"schedule": (ScheduleEntry(valid_from=dt.date(2026, 3, 1), hours=SHORT_FRIDAY),)}
    )
    plans = [plan(leaves=[{"date": "2026-02-27", "bucket": "rol"}])]
    with pytest.raises(PlanError, match="leave on 2026-02-27: no work schedule before 2026-03-01"):
        check_plans(plans, BUCKETS, WorkCalendar(late_start))
