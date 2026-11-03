import datetime as dt
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from vacation_calendar.holiday_set import CountryBase
from vacation_calendar.ledger import (
    Ledger,
    MonthRow,
    NegativeBalanceError,
    NoticeKind,
    build_ledger,
)
from vacation_calendar.models import Absence, Accrual, Bucket, Config, PayslipMode, Unit, YearPlan
from vacation_calendar.schedule import ScheduleEntry, WeekHours
from vacation_calendar.work_calendar import CalendarConfig, Closure

FULL_TIME = WeekHours(
    mon=Decimal(8), tue=Decimal(8), wed=Decimal(8), thu=Decimal(8), fri=Decimal(8)
)
NO_HOLIDAYS = CalendarConfig(
    schedule=(ScheduleEntry(valid_from=dt.date(2020, 1, 1), hours=FULL_TIME),)
)
SINCE_2020 = dt.date(2020, 1, 1)
BUCKETS = {
    "vacation": Bucket(
        name="Vacation", accrual=(Accrual(valid_from=SINCE_2020, monthly=Decimal("13.33")),)
    ),
    "rol": Bucket(name="ROL", accrual=(Accrual(valid_from=SINCE_2020, monthly=Decimal("8.67")),)),
    "urlaub": Bucket(
        name="Urlaub", unit=Unit.DAYS, accrual=(Accrual(valid_from=SINCE_2020, yearly=Decimal(30)),)
    ),
    "ferias": Bucket(name="Férias", unit=Unit.CALENDAR_DAYS),
}
CONFIG = Config(calendar=NO_HOLIDAYS, buckets=BUCKETS)
TODAY = dt.date(2026, 1, 1)


def plan(year: int = 2026, **fields: object) -> YearPlan:
    return YearPlan.model_validate({"year": year, **fields})


def ledger(
    *plans: YearPlan, config: Config = CONFIG, until: dt.date | None = None, strict: bool = False
) -> Ledger:
    return build_ledger(config, plans, today=TODAY, until=until, strict=strict)


def rows(result: Ledger, bucket: str) -> dict[str, MonthRow]:
    return {f"{row.month:%Y-%m}": row for row in result.rows[bucket]}


def closings(result: Ledger, bucket: str) -> list[Decimal]:
    return [row.closing for row in result.rows[bucket]]


def test_leaves_in_several_months_add_up() -> None:
    # the 2023 prototype subtracted only the month's own use from the cumulative accrual
    result = ledger(
        plan(
            carry_over={"rol": 10},
            leaves=[
                {"date": "2026-02-03", "hours": 4, "bucket": "rol", "state": "taken"},
                {"date": "2026-03-03", "hours": 4, "bucket": "rol", "state": "taken"},
            ],
        ),
        until=dt.date(2026, 3, 1),
    )
    assert closings(result, "rol") == [Decimal("18.67"), Decimal("23.34"), Decimal("28.01")]


def test_taken_and_planned_are_kept_apart_and_both_count() -> None:
    result = ledger(
        plan(
            leaves=[
                {
                    "start": "2026-01-05",
                    "end": "2026-01-06",
                    "bucket": "vacation",
                    "state": "taken",
                },
                {"date": "2026-01-07", "hours": 2, "bucket": "vacation"},
            ]
        )
    )
    january = rows(result, "vacation")["2026-01"]
    assert (january.taken, january.planned, january.closing) == (
        Decimal(16),
        Decimal(2),
        Decimal("-4.67"),
    )


def test_carry_over_is_computed_from_the_previous_year() -> None:
    result = ledger(plan(2025, carry_over={"rol": 0}), plan(2026))
    january = rows(result, "rol")["2026-01"]
    assert (january.opening, january.adjustment) == (Decimal("104.04"), Decimal(0))


def test_a_given_carry_over_re_anchors_the_computed_one() -> None:
    result = ledger(plan(2025, carry_over={"rol": 0}), plan(2026, carry_over={"rol": 100}))
    january = rows(result, "rol")["2026-01"]
    assert (january.opening, january.adjustment, january.closing) == (
        Decimal("104.04"),
        Decimal("-4.04"),
        Decimal("108.67"),
    )


def test_a_leave_across_new_year_counts_in_each_month() -> None:
    christmas = {"start": "2025-12-29", "end": "2026-01-02", "bucket": "vacation"}
    result = ledger(plan(2025, carry_over={"vacation": 0}, leaves=[christmas]), plan(2026))
    by_month = rows(result, "vacation")
    assert (by_month["2025-12"].planned, by_month["2026-01"].planned) == (Decimal(24), Decimal(16))


@pytest.mark.parametrize(
    ("mode", "closing", "adjustment", "april"),
    [
        (PayslipMode.CHECK, Decimal("26.01"), Decimal(0), Decimal("34.68")),
        (PayslipMode.OVERRIDE, Decimal(26), Decimal("-0.01"), Decimal("34.67")),
    ],
)
def test_payslip_checks_or_overrides(
    mode: PayslipMode, closing: Decimal, adjustment: Decimal, april: Decimal
) -> None:
    payslip = {"month": "2026-03", "mode": mode, "balances": {"rol": 26}}
    by_month = rows(ledger(plan(carry_over={"rol": 0}, payslips=[payslip])), "rol")
    march = by_month["2026-03"]
    assert (march.payslip, march.delta) == (Decimal(26), Decimal("-0.01"))
    assert (march.closing, march.adjustment, by_month["2026-04"].closing) == (
        closing,
        adjustment,
        april,
    )


def test_payslip_mismatch_notice_only_in_check_mode() -> None:
    def kinds(mode: str) -> list[NoticeKind]:
        payslip = {"month": "2026-03", "mode": mode, "balances": {"rol": 26}}
        result = ledger(plan(carry_over={"rol": 0}, payslips=[payslip]))
        return [notice.kind for notice in result.notices if notice.bucket == "rol"]

    assert kinds("check") == [NoticeKind.PAYSLIP_MISMATCH]
    assert kinds("override") == []


LAGGED = CONFIG.model_copy(update={"payslip_lag": 1})


@pytest.mark.parametrize(
    ("config", "deltas"),
    [
        (LAGGED, {"2026-02": Decimal(0), "2026-03": Decimal(0)}),
        (CONFIG, {"2026-02": Decimal(8), "2026-03": Decimal(0)}),
    ],
)
def test_a_lagging_payslip_counts_the_leave_one_month_late(
    config: Config, deltas: dict[str, Decimal]
) -> None:
    # February's payslip shows two months of accrual but not February's 8 h of leave
    payslips = [
        {"month": "2026-02", "mode": "check", "balances": {"rol": "17.34"}},
        {"month": "2026-03", "mode": "check", "balances": {"rol": "18.01"}},
    ]
    leave = {"date": "2026-02-03", "bucket": "rol", "state": "taken"}
    result = ledger(plan(carry_over={"rol": 0}, leaves=[leave], payslips=payslips), config=config)
    by_month = rows(result, "rol")
    assert {month: by_month[month].delta for month in deltas} == deltas
    assert by_month["2026-02"].closing == Decimal("9.34")


def test_a_lagging_mismatch_notice_shows_what_the_payslip_should_say() -> None:
    payslip = {"month": "2026-02", "mode": "check", "balances": {"rol": "18.34"}}
    leave = {"date": "2026-02-03", "bucket": "rol", "state": "taken"}
    result = ledger(plan(carry_over={"rol": 0}, leaves=[leave], payslips=[payslip]), config=LAGGED)
    messages = [notice.message for notice in result.notices if notice.bucket == "rol"]
    assert messages == ["rol: payslip 2026-02 shows 18.34, computed 17.34 (+1.00)"]


def test_a_lagging_override_keeps_the_unseen_leave() -> None:
    payslip = {"month": "2026-02", "mode": "override", "balances": {"rol": 20}}
    leave = {"date": "2026-02-03", "bucket": "rol", "state": "taken"}
    result = ledger(plan(carry_over={"rol": 0}, leaves=[leave], payslips=[payslip]), config=LAGGED)
    february = rows(result, "rol")["2026-02"]
    assert (february.delta, february.adjustment) == (Decimal("2.66"), Decimal("2.66"))
    assert february.closing == 12  # the payslip's 20 minus the 8 h it does not count yet


def test_a_two_month_lag_reaches_back_into_the_previous_year_but_not_before_the_ledger() -> None:
    config = CONFIG.model_copy(update={"payslip_lag": 2})
    december = {"date": "2026-12-07", "bucket": "rol", "state": "taken"}
    january = {"date": "2027-01-04", "hours": 2, "bucket": "rol", "state": "taken"}
    payslips_2026 = [{"month": "2026-01", "mode": "check", "balances": {"rol": "8.67"}}]
    leave_2026 = {"date": "2026-01-05", "hours": 1, "bucket": "rol", "state": "taken"}
    payslips_2027 = [{"month": "2027-01", "mode": "check", "balances": {"rol": "111.71"}}]
    result = ledger(
        plan(carry_over={"rol": 0}, leaves=[leave_2026, december], payslips=payslips_2026),
        plan(2027, leaves=[january], payslips=payslips_2027),
        config=config,
    )
    by_month = rows(result, "rol")
    assert by_month["2026-01"].delta == 0  # only January's 1 h is unseen: nothing before it
    assert by_month["2027-01"].closing == Decimal("101.71")  # 104.04 - 1 - 8 + 8.67 - 2
    assert by_month["2027-01"].delta == 0  # December's 8 h and January's 2 h are unseen


def per_year_credits(total: str, valid_from: dt.date = dt.date(2026, 1, 1)) -> list[Decimal]:
    bucket = Bucket(
        name="Leave", accrual=(Accrual(valid_from=valid_from, per_year=Decimal(total)),)
    )
    config = CONFIG.model_copy(update={"buckets": {"leave": bucket}})
    return [
        row.accrued for row in ledger(plan(carry_over={"leave": 0}), config=config).rows["leave"]
    ]


@pytest.mark.parametrize(
    ("total", "expected"),
    [
        # a payroll's 160 h and 104 h a year: the leftover cents go to the first months
        ("160", ["13.34"] * 4 + ["13.33"] * 8),
        ("104", ["8.66"] * 4 + ["8.67"] * 8),
        ("30", ["2.5"] * 12),
        ("0.06", ["0.00"] * 6 + ["0.01"] * 6),  # the most cents a rounded twelfth can leave over
    ],
)
def test_a_yearly_total_is_credited_monthly_and_sums_exactly(
    total: str, expected: list[str]
) -> None:
    assert per_year_credits(total) == [Decimal(credit) for credit in expected]


def test_a_yearly_total_from_mid_year_follows_the_calendar_months() -> None:
    march_on = per_year_credits("160", dt.date(2026, 3, 1))
    assert march_on == [Decimal(0)] * 2 + [Decimal("13.34")] * 2 + [Decimal("13.33")] * 8


@given(st.decimals(min_value=0, max_value=1000, places=2, allow_nan=False, allow_infinity=False))
def test_twelve_monthly_credits_sum_to_the_yearly_total(total: Decimal) -> None:
    assert sum(per_year_credits(str(total)), Decimal(0)) == total


def test_yearly_grant_and_half_days_in_a_days_bucket() -> None:
    result = ledger(
        plan(
            carry_over={"urlaub": 2},
            leaves=[
                {"date": "2026-02-02", "hours": 4, "bucket": "urlaub"},
                {"start": "2026-02-09", "end": "2026-02-13", "bucket": "urlaub"},
            ],
        )
    )
    by_month = rows(result, "urlaub")
    assert (by_month["2026-01"].accrued, by_month["2026-02"].accrued) == (Decimal(30), Decimal(0))
    assert by_month["2026-02"].closing == Decimal("26.5")


def test_seniority_grants_on_the_hire_anniversary() -> None:
    grants = (
        Accrual(valid_from=dt.date(2025, 4, 1), yearly=Decimal(10)),
        Accrual(valid_from=dt.date(2026, 4, 1), yearly=Decimal(11)),
    )
    config = CONFIG.model_copy(
        update={"buckets": {"nenkyu": Bucket(name="Nenkyu", accrual=grants)}}
    )
    result = ledger(plan(2025, carry_over={"nenkyu": 0}), plan(2026), config=config)
    accrued = {month: row.accrued for month, row in rows(result, "nenkyu").items() if row.accrued}
    assert accrued == {"2025-04": Decimal(10), "2026-04": Decimal(11)}


def test_calendar_days_count_weekends() -> None:
    result = ledger(plan(leaves=[{"start": "2026-08-01", "end": "2026-08-30", "bucket": "ferias"}]))
    assert rows(result, "ferias")["2026-08"].planned == Decimal(30)


def test_closures_use_their_bucket_and_holidays_use_nothing() -> None:
    shutdown = Closure(
        name="Summer shutdown",
        bucket="vacation",
        start=dt.date(2026, 8, 10),
        end=dt.date(2026, 8, 14),
    )
    calendar = NO_HOLIDAYS.model_copy(
        update={"base": CountryBase(country="IT"), "closures": (shutdown,)}
    )
    config = CONFIG.model_copy(update={"calendar": calendar})
    result = ledger(
        plan(
            leaves=[
                {"start": "2026-04-06", "end": "2026-04-10", "bucket": "vacation"},
                {"date": "2026-08-12", "hours": 4, "bucket": "vacation"},  # inside the closure
            ]
        ),
        config=config,
    )
    by_month = rows(result, "vacation")
    august = by_month["2026-08"]
    assert (august.closures, august.planned) == (Decimal(40), Decimal(0))
    assert august.closing == by_month["2026-07"].closing + Decimal("13.33") - 40
    assert by_month["2026-04"].planned == Decimal(32)  # Easter Monday is a holiday
    assert NoticeKind.NO_LEAVE_USED in {notice.kind for notice in result.notices}


def test_lots_use_the_oldest_year_first() -> None:
    result = ledger(
        plan(
            carry_over={"vacation": 10},
            leaves=[
                {"date": "2026-01-05", "bucket": "vacation"},
                {"date": "2026-02-02", "hours": 4, "bucket": "vacation"},
            ],
        )
    )
    by_month = rows(result, "vacation")
    assert by_month["2026-01"].lots == {2025: Decimal(2), 2026: Decimal("13.33")}
    assert by_month["2026-02"].lots == {2026: Decimal("24.66")}


def test_overdraft_is_paid_back_by_the_next_accrual() -> None:
    result = ledger(
        plan(carry_over={"vacation": 2}, leaves=[{"date": "2026-01-05", "bucket": "vacation"}])
    )
    assert rows(result, "vacation")["2026-01"].lots == {2026: Decimal("7.33")}


def test_notices() -> None:
    result = build_ledger(
        CONFIG,
        [
            plan(
                carry_over={"vacation": 0, "rol": 0, "urlaub": 0},
                leaves=[
                    {"date": "2026-01-10", "bucket": "rol"},  # a Saturday
                    {"start": "2026-02-02", "end": "2026-02-06", "bucket": "rol"},
                ],
            )
        ],
        today=dt.date(2026, 2, 9),
        until=dt.date(2026, 2, 1),
    )
    assert [(notice.kind, notice.day, notice.message) for notice in result.notices] == [
        (
            NoticeKind.NO_CARRY_OVER,
            dt.date(2026, 1, 1),
            "ferias: no carry-over into 2026; starting from 0",
        ),
        (
            NoticeKind.PAST_PLANNED,
            dt.date(2026, 1, 10),
            "leave on 2026-01-10 is still planned; mark it taken or remove it",
        ),
        (
            NoticeKind.NO_LEAVE_USED,
            dt.date(2026, 1, 10),
            "leave on 2026-01-10 has no working day, so it uses no rol",
        ),
        (
            NoticeKind.PAST_PLANNED,
            dt.date(2026, 2, 2),
            "leave 2026-02-02..2026-02-06 is still planned; mark it taken or remove it",
        ),
        (
            NoticeKind.NEGATIVE_BALANCE,
            dt.date(2026, 2, 28),
            "rol: balance -22.66 at the end of 2026-02",
        ),
    ]


def test_strict_mode_raises_on_a_negative_balance() -> None:
    overdrawn = plan(
        carry_over={"rol": 0},
        leaves=[{"start": "2026-02-02", "end": "2026-02-06", "bucket": "rol"}],
    )
    with pytest.raises(NegativeBalanceError, match=r"rol: balance -22\.66 at the end of 2026-02"):
        ledger(overdrawn, strict=True)


def test_months_after_the_last_plan_follow_the_accrual() -> None:
    result = ledger(plan(carry_over={"rol": 0}), until=dt.date(2027, 2, 15))
    assert closings(result, "rol")[-3:] == [Decimal("104.04"), Decimal("112.71"), Decimal("121.38")]


@pytest.mark.parametrize(
    ("plans", "until", "message"),
    [
        ((), None, "at least one year plan"),
        ((plan(),), dt.date(2025, 12, 31), "before the first plan year 2026"),
    ],
)
def test_invalid_ledger_requests(
    plans: tuple[YearPlan, ...], until: dt.date | None, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        build_ledger(CONFIG, plans, today=TODAY, until=until)


WEEKDAYS_2026 = st.dates(dt.date(2026, 1, 1), dt.date(2026, 12, 31)).filter(
    lambda day: day.weekday() < 5
)
AMOUNTS = st.decimals(min_value=-50, max_value=200, places=2, allow_nan=False, allow_infinity=False)


@st.composite
def year_plans(draw: st.DrawFn) -> YearPlan:
    days = draw(st.lists(WEEKDAYS_2026, unique=True, max_size=20))
    leaves = [
        {
            "date": day,
            "bucket": draw(st.sampled_from(["vacation", "rol", "urlaub", "ferias"])),
            "state": draw(st.sampled_from(["planned", "taken"])),
        }
        for day in days
    ]
    for leave in leaves:
        if leave["bucket"] != "ferias":
            leave["hours"] = draw(st.none() | st.integers(1, 8))
    months = draw(st.lists(st.integers(1, 12), unique=True, max_size=4))
    payslips = [
        {
            "month": f"2026-{month:02}",
            "mode": draw(st.sampled_from(["check", "override"])),
            "balances": {"vacation": draw(AMOUNTS), "urlaub": draw(AMOUNTS)},
        }
        for month in months
    ]
    carry_over = draw(st.dictionaries(st.sampled_from(list(BUCKETS)), AMOUNTS))
    return plan(carry_over=carry_over, leaves=leaves, payslips=payslips)


@given(year_plans())
def test_every_row_balances(year_plan: YearPlan) -> None:
    result = ledger(year_plan)
    for bucket, bucket_rows in result.rows.items():
        previous = None
        for row in bucket_rows:
            used = row.taken + row.planned + row.closures
            assert row.closing == row.opening + row.accrued - used + row.adjustment, bucket
            assert sum(row.lots.values(), Decimal(0)) == row.closing, bucket
            if previous is not None:
                assert row.opening == previous.closing, bucket
            previous = row


@given(year_plans(), st.integers(0, 3))
def test_an_override_sets_the_closing_to_the_payslip(year_plan: YearPlan, lag: int) -> None:
    result = ledger(year_plan, config=CONFIG.model_copy(update={"payslip_lag": lag}))
    for payslip in year_plan.payslips:
        for bucket, balance in payslip.balances.items():
            index = payslip.month.month - 1
            recent = result.rows[bucket][max(0, index - lag + 1) : index + 1] if lag else ()
            unseen = sum((row.taken + row.planned + row.closures for row in recent), Decimal(0))
            row = result.rows[bucket][index]
            if payslip.mode is PayslipMode.OVERRIDE:
                assert row.closing + unseen == balance
            else:
                assert row.closing + unseen == balance - (row.delta or 0)


ABSENT = CONFIG.model_copy(update={"absences": {"donation": Absence(name="Blood donation")}})


def test_absences_use_no_balance_and_are_counted_by_working_day() -> None:
    leaves = [
        {"start": "2026-03-06", "end": "2026-03-09", "bucket": "donation", "state": "taken"},
        {"date": "2026-04-01", "hours": 2, "bucket": "donation", "state": "taken"},
        {"date": "2026-04-02", "hours": 4, "bucket": "donation"},
    ]
    result = ledger(plan(carry_over={"rol": 0}, leaves=leaves), config=ABSENT)
    assert result.absences == {
        "donation": {dt.date(2026, 3, 1): Decimal(2), dt.date(2026, 4, 1): Decimal("0.75")}
    }  # Friday and Monday, not the weekend; 2 h and 4 h of 8 h days
    assert result.rows == ledger(plan(carry_over={"rol": 0})).rows


def test_absences_get_the_leave_notices() -> None:
    leaves = [
        {"date": "2025-12-27", "bucket": "donation"},  # a Saturday, before today
    ]
    result = ledger(plan(2025, carry_over=dict.fromkeys(BUCKETS, 0), leaves=leaves), config=ABSENT)
    assert [notice.message for notice in result.notices] == [
        "leave on 2025-12-27 is still planned; mark it taken or remove it",
        "leave on 2025-12-27 has no working day for donation",
    ]
    assert result.absences == {}


def test_absences_after_the_ledger_end_are_not_counted() -> None:
    leave = {"start": "2026-12-31", "end": "2027-01-04", "bucket": "donation", "state": "taken"}
    result = ledger(plan(carry_over={"rol": 0}, leaves=[leave]), config=ABSENT)
    assert result.absences == {"donation": {dt.date(2026, 12, 1): Decimal(1)}}
