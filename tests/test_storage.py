import datetime as dt
from decimal import Decimal
from pathlib import Path

import platformdirs
import pytest

from vacation_calendar.holiday_set import CountryBase
from vacation_calendar.ledger import build_ledger
from vacation_calendar.models import Leave, LeaveState, PayslipEntry, PlanError, Unit
from vacation_calendar.storage import (
    ENV_VAR,
    StorageError,
    YearFile,
    check_folder,
    copy_folder,
    data_dir,
    read_config,
    read_plans,
    write_template,
    year_files,
)

EXAMPLES = Path(__file__).parents[1] / "examples"

CONFIG = """\
schema = 1

[holidays]
base = { country = "IT", subdiv = "BO", language = "en_US" }

[[holidays.add]]  # company holiday
name = "Christmas Eve"
rule = "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=24"

[[holidays.remove]]
name = "Saint Stephen's Day"

[[closures]]
name = "Summer shutdown"
start = 2026-08-10
end = 2026-08-14
bucket = "vacation"

[[schedule]]
valid_from = 2025-01-01
hours = { mon = 8, tue = 8, wed = 8, thu = 8, fri = 8 }

[buckets.vacation]
name = "Ferie"
accrual = [{ valid_from = 2025-01-01, monthly = 13.33 }]

[buckets.urlaub]
name = "Urlaub"
unit = "days"
accrual = [{ valid_from = 2025-01-01, yearly = 30 }]
"""

YEAR = """\
schema = 1  # file format

[carry_over]  # from the December payslip
vacation = 25.5

[[leaves]]  # first one
date = 2026-03-06
hours = 4
bucket = "vacation"
state = "taken"

# summer
[[leaves]]
start = 2026-08-17
end = 2026-08-21
bucket = "vacation"  # beach

[[payslips]]  # September
month = "2026-09"
mode = "check"
balances = { vacation = 61.2 }  # as printed
"""


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    (tmp_path / "config.toml").write_text(CONFIG, encoding="utf-8")
    (tmp_path / "2026.toml").write_text(YEAR, encoding="utf-8")
    return tmp_path


def problems(error: pytest.ExceptionInfo[StorageError]) -> list[str]:
    return list(error.value.problems)


def test_data_dir_prefers_the_override_then_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(ENV_VAR, str(tmp_path / "env"))
    assert data_dir(tmp_path / "flag") == tmp_path / "flag"
    assert data_dir() == tmp_path / "env"
    monkeypatch.setenv(ENV_VAR, "")
    assert data_dir() == platformdirs.user_data_path("vacation-calendar", appauthor=False)


def test_config_puts_holidays_closures_and_schedule_in_the_calendar(folder: Path) -> None:
    config = read_config(folder)
    assert config.calendar.base == CountryBase(country="IT", subdiv="BO", language="en_US")
    assert [addition.name for addition in config.calendar.add] == ["Christmas Eve"]
    assert [removal.name for removal in config.calendar.remove] == ["Saint Stephen's Day"]
    assert [closure.name for closure in config.calendar.closures] == ["Summer shutdown"]
    assert config.calendar.schedule[0].hours.mon == 8
    assert config.buckets["urlaub"].unit is Unit.DAYS
    assert config.buckets["vacation"].accrual[0].monthly == Decimal("13.33")


def test_the_payslip_lag_is_a_top_level_setting(folder: Path) -> None:
    assert read_config(folder).payslip_lag == 0
    (folder / "config.toml").write_text(f"payslip_lag = 1\n{CONFIG}", encoding="utf-8")
    assert read_config(folder).payslip_lag == 1
    (folder / "config.toml").write_text(f"payslip_lag = 13\n{CONFIG}", encoding="utf-8")
    with pytest.raises(StorageError) as error:
        read_config(folder)
    assert problems(error) == ["payslip_lag: Input should be less than or equal to 12"]


def test_an_empty_base_means_no_public_holidays(folder: Path) -> None:
    text = CONFIG.replace('{ country = "IT", subdiv = "BO", language = "en_US" }', "{}")
    (folder / "config.toml").write_text(text, encoding="utf-8")
    assert read_config(folder).calendar.base is None


def test_config_errors_name_the_place_in_the_file(folder: Path) -> None:
    text = (
        CONFIG.replace('subdiv = "BO"', 'subdiv = "XX"')
        .replace("monthly = 13.33", "monthly = -1")
        .replace("{ mon = 8,", "{ mon = 30,")
        .replace('name = "Christmas Eve"', 'name = "Christmas Eve"\ndate = 2026-12-24')
    )
    (folder / "config.toml").write_text("colour = 'blue'\n" + text, encoding="utf-8")
    with pytest.raises(StorageError) as error:
        read_config(folder)
    found = problems(error)
    assert found == [
        "holidays.base: subdivision 'XX' is not supported for IT",
        "schedule #1.hours.mon: Input should be less than or equal to 24",
        "holidays.add #1: give exactly one of: date, start and end, rule",
        "buckets.vacation.accrual #1.monthly: Input should be greater than or equal to 0",
        "colour: Extra inputs are not permitted",
    ]
    assert str(error.value).startswith("config.toml: ")


def test_an_empty_list_is_reported_when_nothing_in_it_failed(folder: Path) -> None:
    text = CONFIG.split("[buckets.", maxsplit=1)[0] + "[buckets]\n"
    (folder / "config.toml").write_text(text, encoding="utf-8")
    with pytest.raises(StorageError) as error:
        read_config(folder)
    assert problems(error) == [
        "buckets: Dictionary should have at least 1 item after validation, not 0"
    ]


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("[buckets]\n", "no schema; this version reads files with schema = 1"),
        ("schema = 2\n", "schema 2; this version reads files with schema = 1"),
        ("schema = 1\nbuckets = [\n", "not valid TOML: "),
    ],
)
def test_files_need_valid_toml_and_schema_1(folder: Path, text: str, message: str) -> None:
    (folder / "config.toml").write_text(text, encoding="utf-8")
    with pytest.raises(StorageError) as error:
        read_config(folder)
    assert problems(error)[0].startswith(message)


def test_a_missing_config_is_reported(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match=r"config\.toml: not found in "):
        read_config(tmp_path)


def test_year_plans_take_their_year_from_the_file_name(folder: Path) -> None:
    (folder / "2027.toml").write_text("schema = 1\n", encoding="utf-8")
    (folder / "notes.toml").write_text("anything = true\n", encoding="utf-8")
    (folder / "2025.toml.bak").write_text("", encoding="utf-8")
    assert year_files(folder) == [2026, 2027]
    plans = read_plans(folder)
    assert [plan.year for plan in plans] == [2026, 2027]
    assert plans[0].carry_over == {"vacation": Decimal("25.5")}
    assert plans[0].leaves[1].last == dt.date(2026, 8, 21)
    assert plans[0].payslips[0].month == dt.date(2026, 9, 1)


def test_a_year_file_may_not_name_its_year(folder: Path) -> None:
    (folder / "2026.toml").write_text("schema = 1\nyear = 2026\n", encoding="utf-8")
    with pytest.raises(StorageError, match="year: comes from the file name"):
        read_plans(folder)


def test_year_errors_count_entries_from_one(folder: Path) -> None:
    text = YEAR.replace('bucket = "vacation"  # beach', "")
    (folder / "2026.toml").write_text(text, encoding="utf-8")
    with pytest.raises(StorageError) as error:
        read_plans(folder)
    assert problems(error) == ["leaves #2.bucket: Field required"]


def test_saving_an_unchanged_file_keeps_it_as_it_was(folder: Path) -> None:
    YearFile.open(folder, 2026).save()
    assert (folder / "2026.toml").read_text(encoding="utf-8") == YEAR


def test_edits_keep_comments_and_order_entries_by_date(folder: Path) -> None:
    year = YearFile.open(folder, 2026)
    year.add_leave(Leave(date=dt.date(2026, 5, 4), hours=Decimal("2.5"), bucket="vacation"))
    year.add_leave(Leave(start=dt.date(2026, 12, 28), end=dt.date(2027, 1, 5), bucket="vacation"))
    year.add_leave(Leave(date=dt.date(2026, 1, 2), bucket="vacation", state=LeaveState.TAKEN))
    year.set_payslip(PayslipEntry.model_validate({"month": "2026-03", "mode": "override",
                                                  "balances": {"vacation": "40.00"}}))  # fmt: skip
    year.set_payslip(PayslipEntry.model_validate({"month": "2026-09", "mode": "check",
                                                  "balances": {"vacation": "60.5"}}))  # fmt: skip
    assert year.take(dt.date(2026, 8, 19)) == [read_plans(folder)[0].leaves[1]]
    year.save()
    assert (
        (folder / "2026.toml").read_text(encoding="utf-8")
        == """\
schema = 1  # file format

[carry_over]  # from the December payslip
vacation = 25.5

[[leaves]]
date = 2026-01-02
bucket = "vacation"
state = "taken"

[[leaves]]  # first one
date = 2026-03-06
hours = 4
bucket = "vacation"
state = "taken"

[[leaves]]
date = 2026-05-04
hours = 2.5
bucket = "vacation"
state = "planned"

# summer
[[leaves]]
start = 2026-08-17
end = 2026-08-21
bucket = "vacation"  # beach
state = "taken"

[[leaves]]
start = 2026-12-28
end = 2027-01-05
bucket = "vacation"
state = "planned"

[[payslips]]
month = "2026-03"
mode = "override"
balances = {vacation = 40}

[[payslips]]  # September
month = "2026-09"
mode = "check"
balances = { vacation = 60.5 }  # as printed
"""
    )


def test_take_marks_only_planned_leaves_of_the_bucket(folder: Path) -> None:
    year = YearFile.open(folder, 2026)
    assert year.take(dt.date(2026, 8, 19), bucket="urlaub") == []
    assert year.take(dt.date(2026, 3, 6)) == []  # already taken
    assert year.take(dt.date(2026, 8, 22)) == []  # the day after
    assert [leave.state for leave in year.plan().leaves] == [LeaveState.TAKEN, LeaveState.PLANNED]


def test_a_new_year_file_starts_with_the_schema(tmp_path: Path) -> None:
    year = YearFile.open(tmp_path / "data", 2027)
    year.add_leave(Leave(date=dt.date(2027, 2, 1), bucket="vacation"))
    year.save()
    assert (
        (tmp_path / "data" / "2027.toml").read_text(encoding="utf-8")
        == """\
schema = 1

[[leaves]]
date = 2027-02-01
bucket = "vacation"
state = "planned"
"""
    )


def test_check_folder_checks_an_edit_before_it_is_saved(folder: Path) -> None:
    year = YearFile.open(folder, 2026)
    year.add_leave(Leave(date=dt.date(2026, 3, 6), bucket="rol"))
    with pytest.raises(PlanError, match="unknown bucket 'rol'"):
        check_folder(folder, year)
    config, plans = check_folder(folder)
    assert set(config.buckets) == {"vacation", "urlaub"}
    assert [plan.year for plan in plans] == [2026]


# Each example's closing balances on 31 March and 31 December 2026, counted by hand from its files.
EXAMPLE_CLOSINGS = {
    # 5 granted; Tuesday to Saturday, a make-up working day, uses 5
    "cn": {"annual": ("0", "0")},
    # 3 + 30 - 18 leave - 5 closure days (24, 28-31 December)
    "de-by": {"urlaub": ("32", "10")},
    # 22 granted, 3 + 15 + 4 used
    "es-md": {"vacaciones": ("20", "0")},
    # 12.5 + 12 x 2.08 - 24; 2 + 10 - 2
    "fr": {"conges": ("18.74", "13.46"), "rtt": ("12", "10")},
    # 6 - 5, + 20 on 1 April, - 4 - 4 - 5 - 3
    "gb-eng": {"annual": ("1", "5")},
    # 42.5 + 160 - 88 leave - 40 closure; 30.17 + 104 - 20 (13.34 and 8.66 a month to April)
    "it-bo": {"vacation": ("66.52", "74.50"), "rol": ("52.15", "114.17")},
    # 9 - 2 - 3, + 12 on 1 October, - 1 (29, 30 December are company holidays)
    "jp": {"nenkyu": ("9", "15")},
    # 3 + 12 x 1.25 - 1 - 2 - 3 (26, 27 November are holidays) - 4; 1 - 1
    "us": {"pto": ("6.75", "8"), "floating": ("1", "0")},
}


def test_every_example_has_expected_closings() -> None:
    assert sorted(path.name for path in EXAMPLES.iterdir()) == sorted(EXAMPLE_CLOSINGS)


@pytest.mark.parametrize("name", sorted(EXAMPLE_CLOSINGS))
def test_the_examples_load_and_build_a_ledger(name: str) -> None:
    config, plans = check_folder(EXAMPLES / name)
    ledger = build_ledger(config, plans, today=dt.date(2026, 11, 3))
    closings = {key: (rows[2].closing, rows[11].closing) for key, rows in ledger.rows.items()}
    assert closings == {
        key: (Decimal(march), Decimal(december))
        for key, (march, december) in EXAMPLE_CLOSINGS[name].items()
    }
    assert ledger.notices == ()


def test_the_template_loads_with_its_placeholders(tmp_path: Path) -> None:
    path = write_template(tmp_path, CountryBase(country="ES", subdiv="MD", language="en_US"), 2027)
    assert path == tmp_path / "config.toml"
    config = read_config(tmp_path)
    assert config.calendar.base == CountryBase(country="ES", subdiv="MD", language="en_US")
    assert config.calendar.schedule[0].valid_from == dt.date(2027, 1, 1)
    assert config.buckets["vacation"].unit is Unit.DAYS
    assert config.buckets["vacation"].accrual[0].yearly == 20
    assert config.calendar.closures == ()
    write_template(tmp_path / "it", CountryBase(country="IT"), 2026)
    assert 'base = { country = "IT" }  #' in (tmp_path / "it" / "config.toml").read_text("utf-8")
    with pytest.raises(FileExistsError, match=r"already has config\.toml; not overwriting"):
        write_template(tmp_path, CountryBase(country="IT"), 2026)


def test_copy_folder_copies_config_and_years_and_refuses_to_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "plan"
    assert copy_folder(EXAMPLES / "it-bo", target) == [target / "2026.toml", target / "config.toml"]
    assert read_config(target).buckets["rol"].name == "ROL + ex-festività"
    (target / "2026.toml").unlink()
    with pytest.raises(FileExistsError, match=r"already has config\.toml; not overwriting"):
        copy_folder(EXAMPLES / "it-bo", target)
    assert not (target / "2026.toml").exists()


def test_copy_folder_refuses_a_folder_that_does_not_load(folder: Path, tmp_path: Path) -> None:
    (folder / "2026.toml").write_text(YEAR.replace('bucket = "vacation"  # beach', ""), "utf-8")
    with pytest.raises(StorageError, match=r"leaves #2\.bucket"):
        copy_folder(folder, tmp_path / "plan")
    assert not (tmp_path / "plan").exists()
