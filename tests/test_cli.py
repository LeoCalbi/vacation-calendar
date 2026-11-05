import datetime as dt
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from vacation_calendar import cli
from vacation_calendar.report import CSV_FIELDS
from vacation_calendar.storage import ENV_VAR

NOW = dt.datetime(2026, 11, 3, 12, tzinfo=dt.UTC)

EXAMPLES = Path(__file__).parents[1] / "examples"

runner = CliRunner()


def example(name: str, folder: Path, *, years: bool = False) -> None:
    """Copy an example's config, and its year files if `years`, into `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    for path in (EXAMPLES / name).glob("*.toml"):
        if years or path.name == "config.toml":
            shutil.copyfile(path, folder / path.name)


@pytest.fixture(autouse=True)
def fixed_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_now", lambda: NOW)


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    example("it-bo", tmp_path, years=True)
    return tmp_path


def run(folder: Path, *args: str, code: int = 0) -> str:
    result = runner.invoke(cli.app, ["--data-dir", str(folder), *args])
    assert result.exit_code == code, result.output
    return result.stdout if code == 0 else result.stderr


def table(output: str, title: str) -> dict[str, list[str]]:
    """Return the rows of the table under `title`, by month, as lists of cells."""
    lines = output[output.index(title) :].splitlines()
    header = next(index for index, line in enumerate(lines) if line.strip().startswith("Month"))
    rows: dict[str, list[str]] = {"Month": lines[header].split()}
    for line in lines[header + 2 :]:
        if not line.strip():
            break
        cells = line.split()
        if cells[0][:2] == "20" and len(cells[0]) == 7:
            rows[cells[0]] = cells
    return rows


def tables(output: str) -> int:
    """Count the month-by-month tables in `output` by their header lines."""
    return sum(line.split()[:1] == ["Month"] for line in output.splitlines())


def year_text(folder: Path, year: int = 2026) -> str:
    return (folder / f"{year}.toml").read_text(encoding="utf-8")


def test_init_writes_a_template_for_a_country(tmp_path: Path) -> None:
    output = run(tmp_path, "init", "--country", "de", "--subdiv", "by")
    assert output.startswith(f"wrote {tmp_path / 'config.toml'}\n")
    text = (tmp_path / "config.toml").read_text(encoding="utf-8")
    assert 'base = { country = "DE", subdiv = "BY" }' in text
    assert "valid_from = 2026-01-01" in text
    assert "# [[closures]]" in text
    output = run(tmp_path, "add", "2026-01-07", "2026-01-09")  # one bucket: no --bucket needed
    assert "vacation at the end of 2026: 17.00 days" in output  # 20 - 3; 6 January is a holiday
    error = run(tmp_path, "init", "--country", "IT", code=1)
    assert "already has config.toml; not overwriting" in error


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ([], "give --country (and optionally --subdiv), or --from FOLDER"),
        (["--subdiv", "BO"], "give --country (and optionally --subdiv), or --from FOLDER"),
        (["--from", ".", "--subdiv", "BO"], "give --country (and optionally --subdiv)"),
        (["--country", "IT", "--from", "."], "give --country (and optionally --subdiv)"),
        (["--country", "XX"], "country 'XX' is not supported by the holidays library"),
        (["--country", "IT", "--subdiv", "ZZ"], "subdivision 'ZZ' is not supported for IT"),
    ],
)
def test_init_needs_a_country_or_a_folder(tmp_path: Path, args: list[str], message: str) -> None:
    assert message in run(tmp_path / "plan", "init", *args, code=1)
    assert not (tmp_path / "plan").exists()


def test_init_copies_a_data_folder_and_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "plan"
    output = run(target, "init", "--from", str(EXAMPLES / "de-by"))
    assert output.splitlines()[:2] == [
        f"wrote {target / '2026.toml'}",
        f"wrote {target / 'config.toml'}",
    ]
    assert sorted(path.name for path in target.iterdir()) == ["2026.toml", "config.toml"]
    (target / "2026.toml").write_text("schema = 1\n", encoding="utf-8")
    error = run(target, "init", "--from", str(EXAMPLES / "de-by"), code=1)
    assert "already has 2026.toml, config.toml; not overwriting" in error
    assert (target / "2026.toml").read_text(encoding="utf-8") == "schema = 1\n"


def test_init_refuses_a_folder_that_does_not_load(tmp_path: Path) -> None:
    (tmp_path / "broken").mkdir()
    (tmp_path / "broken" / "config.toml").write_text("schema = 2\n", encoding="utf-8")
    error = run(tmp_path / "plan", "init", "--from", str(tmp_path / "broken"), code=1)
    assert "config.toml: schema 2" in error
    assert not (tmp_path / "plan").exists()


def test_the_data_folder_comes_from_the_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(ENV_VAR, str(tmp_path))
    result = runner.invoke(cli.app, ["init", "--country", "FR"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "config.toml").exists()


def test_holidays_lists_holidays_closures_and_make_up_days(tmp_path: Path) -> None:
    example("it-bo", tmp_path / "it")
    output = run(tmp_path / "it", "holidays", "2026")
    assert "2026-01-06 Tue holiday Epifania" in [
        " ".join(line.split()) for line in output.splitlines()
    ]
    assert "Epifania" in output
    assert "closure, uses vacation" in output
    example("cn", tmp_path / "cn")
    assert "make-up working day" in run(tmp_path / "cn", "holidays", "2026")
    assert "make-up working day" not in output


def test_holidays_default_to_this_year_and_skip_days_before_the_schedule(folder: Path) -> None:
    assert "Days off in 2026" in run(folder, "holidays")
    output = run(folder, "holidays", "2024")
    assert "Capodanno" in output
    assert "closure" not in output  # the schedule starts in 2025


def test_add_writes_the_leave_and_shows_the_year_end_balance(folder: Path) -> None:
    output = run(folder, "add", "2026-11-16", "2026-11-18", "--bucket", "vacation")
    assert "added leave 2026-11-16..2026-11-18 (planned) from vacation to" in output
    assert "vacation at the end of 2026: 50.50 h" in output  # 74.50 - 3 x 8
    output = run(folder, "add", "2026-11-19", "-b", "rol", "--hours", "4", "--taken")
    assert "rol at the end of 2026: 110.17 h" in output
    text = year_text(folder)
    assert 'start = 2026-11-16\nend = 2026-11-18\nbucket = "vacation"\nstate = "planned"' in text
    assert 'date = 2026-11-19\nhours = 4\nbucket = "rol"\nstate = "taken"' in text


def test_add_needs_a_bucket_unless_there_is_only_one(folder: Path, tmp_path: Path) -> None:
    assert "give --bucket, one of: vacation, rol" in run(folder, "add", "2026-11-16", code=1)
    example("gb-eng", tmp_path / "gb")
    output = run(tmp_path / "gb", "add", "2027-01-04")
    assert "added leave on 2027-01-04 (planned) from annual to" in output
    assert (tmp_path / "gb" / "2027.toml").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["2026-11-31", "-b", "rol"], "'2026-11-31' is not a date like 2026-08-17"),
        (["2026-11-30", "-b", "rol", "--hours", "x"], "'x' is not a number"),
        (["2026-11-30", "2026-11-27", "-b", "rol"], "end 2026-11-27 is before start 2026-11-30"),
        (
            ["2026-11-30", "-b", "nope"],
            "leave on 2026-11-30: unknown bucket 'nope'; configured: rol, vacation",
        ),
        (["2026-03-06", "-b", "rol"], "2026-03-06: leaves total 12 h, but the schedule has 8 h"),
    ],
)
def test_add_refuses_a_leave_that_does_not_fit(folder: Path, args: list[str], message: str) -> None:
    before = year_text(folder)
    assert f"error: {message}" in run(folder, "add", *args, code=1)
    assert year_text(folder) == before


def test_take_marks_a_planned_leave_also_from_the_year_before(folder: Path) -> None:
    output = run(folder, "take", "2027-01-04")
    assert output == "taken: leave 2026-12-28..2027-01-05 from vacation\n"
    assert 'end = 2027-01-05\nbucket = "vacation"\nstate = "taken"' in year_text(folder)
    error = run(folder, "take", "2027-01-04", code=1)
    assert "no planned leave includes 2027-01-04" in error
    assert "no planned leave from vacation includes 2026-12-07" in run(
        folder, "take", "2026-12-07", "-b", "vacation", code=1
    )


def test_payslip_check_shows_the_difference(folder: Path) -> None:
    output = run(folder, "payslip", "2026-10", "-b", "vacation=80", "-b", "rol=104.83")
    assert "recorded the 2026-10 payslip (check) in" in output
    assert "vacation: payslip 80.00 h, computed 79.84 (+0.16)" in output
    assert "rol: payslip 104.83 h, computed 104.83 (+0.00)" in output
    assert 'month = "2026-10"\nmode = "check"\nbalances = {vacation = 80, rol = 104.83}' in (
        year_text(folder)
    )


def test_payslip_override_re_anchors(folder: Path) -> None:
    output = run(folder, "payslip", "2026-11", "-b", "vacation=70", "--override")
    assert "vacation: payslip 70.00 h, computed 93.17 (-23.17)" in output
    rows = table(run(folder, "report", "2026-11..2026-12"), "Ferie")
    assert rows["2026-11"][rows["Month"].index("Close")] == "70.00"
    assert rows["2026-12"][rows["Month"].index("Close")] == "51.33"  # 70 + 13.33 - 32


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["2026-10", "-b", "vacation"], "--balance 'vacation': write BUCKET=AMOUNT"),
        (["2026-13", "-b", "vacation=1"], "month must be in 1..12"),
        (["2026-10", "-b", "nope=1"], "payslip 2026-10: unknown bucket 'nope'"),
    ],
)
def test_payslip_refuses_bad_input(folder: Path, args: list[str], message: str) -> None:
    assert message in run(folder, "payslip", *args, code=1)


def test_report_shows_two_decimals_days_and_only_used_columns(folder: Path) -> None:
    output = run(folder, "report")
    vacation, rol = table(output, "Ferie (vacation, hours)"), table(output, "ROL + ex")
    assert vacation["Month"] == [
        "Month", "Open", "Accrued", "Taken", "Planned", "Closures", "Close", "Days", "Payslip"
    ]  # fmt: skip
    assert vacation["2026-08"] == [
        "2026-08", "119.85", "13.33", "40.00", "0.00", "40.00", "53.18", "6.65"
    ]  # fmt: skip
    assert vacation["2026-09"][-1] == "66.51"
    assert "Closures" not in rol["Month"]
    assert rol["2026-12"][1:] == ["113.50", "8.67", "0.00", "8.00", "114.17", "14.27"]
    assert len(vacation) == 13  # header and 12 months
    assert "Notices" not in output


def test_report_days_buckets_have_no_days_column(tmp_path: Path) -> None:
    example("de-by", tmp_path, years=True)
    rows = table(run(tmp_path, "report", "2026-12"), "Urlaub")
    assert rows["Month"] == ["Month", "Open", "Accrued", "Taken", "Planned", "Closures", "Close"]
    assert rows["2026-12"][1:] == ["15.00", "0.00", "0.00", "0.00", "5.00", "10.00"]


def test_report_periods(folder: Path) -> None:
    assert list(table(run(folder, "report", "2026-03..2026-04"), "Ferie"))[1:] == [
        "2026-03",
        "2026-04",
    ]
    rows = table(run(folder, "report", "2026..2027"), "Ferie")
    assert len(rows) == 25
    assert rows["2027-01"][rows["Month"].index("Planned")] == "16.00"  # the leave into 2027
    assert "the data starts in 2026" in run(folder, "report", "2025", code=1)
    assert "ends before it starts" in run(folder, "report", "2026-05..2026-04", code=1)
    assert "'2026-1' is not a year like 2026" in run(folder, "report", "2026-1", code=1)
    assert "month must be in 1..12" in run(folder, "report", "2026-13", code=1)


def test_report_lists_notices_and_lots(folder: Path) -> None:
    run(folder, "payslip", "2026-10", "-b", "vacation=80")
    run(folder, "add", "2026-10-26", "-b", "rol")
    output = run(folder, "report", "2026-10..2026-12", "--lots")
    assert output.split("Notices\n")[1].splitlines() == [
        "2026-10-26  leave on 2026-10-26 is still planned; mark it taken or remove it",
        "2026-10-31  vacation: payslip 2026-10 shows 80, computed 79.84 (+0.16)",
    ]
    rows = table(output, "ROL + ex")
    assert rows["2026-12"][-2:] == ["2025:", "2.17"]
    assert "2026:" in output.split("2026-12")[-1]
    assert "Notices" not in run(folder, "report", "2026-11..2026-12")


def test_report_explains_a_payslip_lag(folder: Path) -> None:
    note = "Payslips count leave 1 month late: Delta compares them with Close plus that leave."
    assert note not in run(folder, "report")
    config = folder / "config.toml"
    config.write_text(
        config.read_text("utf-8").replace("schema = 1\n", "schema = 1\npayslip_lag = 1\n"),
        encoding="utf-8",
    )
    output = run(folder, "report", "2026-08..2026-09")
    assert note in output
    assert "Payslips count" not in run(folder, "report", "2026-08")  # no payslip shown
    rows = table(output, "Ferie")
    assert rows["2026-09"][rows["Month"].index("Close")] == "66.51"
    assert rows["2026-09"][-1] == "66.51"  # September has no leave, so the payslip still matches


def test_absences_are_recorded_reported_and_exported(folder: Path) -> None:
    config = folder / "config.toml"
    text = config.read_text("utf-8") + '\n[absences.donation]\nname = "Blood donation"\n'
    config.write_text(text, encoding="utf-8")
    assert tables(run(folder, "report", "2026-11")) == 2  # the two buckets only
    output = run(folder, "add", "2026-11-20", "-b", "donation", "--taken")
    assert output.splitlines()[-1] == "donation in 2026: 1.00 days"
    assert "donation in 2026: 1.50 days" in run(folder, "add", "2026-12-01", "-b", "donation",
                                                 "--hours", "4")  # fmt: skip
    output = run(folder, "report", "2026-11..2026-12")
    rows = table(output, "Other absences (days)")
    assert rows["Month"] == ["Month", "Blood", "donation"]
    assert (rows["2026-11"][1:], rows["2026-12"][1:]) == (["1.00"], ["0.50"])
    assert tables(run(folder, "report", "2026-10")) == 2
    assert list(table(run(folder, "report", "2026-12"), "Other absences")) == ["Month", "2026-12"]
    assert table(output, "Ferie")["2026-12"][-2:] == ["74.50", "9.31"]  # balances unchanged
    assert "SUMMARY:Blood donation" in run(folder, "export", "ics", "2026-11").splitlines()
    error = run(folder, "payslip", "2026-11", "-b", "donation=1", code=1)
    assert "error: payslip 2026-11: absence 'donation' has no balance" in error


def test_report_strict_fails_on_a_negative_balance(folder: Path) -> None:
    run(folder, "add", "2026-02-02", "2026-02-27", "-b", "vacation")
    error = run(folder, "report", "--strict", code=1)
    assert "error: vacation: balance -106.82 at the end of 2026-02" in error
    assert "-106.82" in run(folder, "report")


def test_report_without_year_files_starts_from_zero(tmp_path: Path) -> None:
    example("fr", tmp_path)
    output = run(tmp_path, "report", "2026-01")
    assert table(output, "Congés")["2026-01"][1:] == ["0.00", "2.08", "0.00", "0.00", "2.08"]
    assert "conges: no carry-over into 2026; starting from 0" in output


def test_export_csv(folder: Path) -> None:
    assert CSV_FIELDS[:3] == ("bucket", "unit", "month")
    output = run(folder, "export", "csv", "2026-12")
    assert output.splitlines() == [
        ",".join(CSV_FIELDS),
        "vacation,hours,2026-12,93.17,13.33,0.00,32.00,0.00,0.00,74.50,9.31,,",
        "rol,hours,2026-12,113.50,8.67,0.00,8.00,0.00,0.00,114.17,14.27,,",
    ]
    assert len(run(folder, "export", "csv").splitlines()) == 1 + 2 * 12


def test_export_csv_leaves_closing_days_empty_for_days_buckets(tmp_path: Path) -> None:
    example("de-by", tmp_path, years=True)
    output = run(tmp_path, "export", "csv", "2026-12")
    assert output.splitlines()[1] == "urlaub,days,2026-12,15.00,0.00,0.00,0.00,5.00,0.00,10.00,,,"


def test_export_ics_to_a_file(folder: Path, tmp_path: Path) -> None:
    target = tmp_path / "leaves.ics"
    assert run(folder, "export", "ics", "2026-12", "-o", str(target)) == f"wrote {target}\n"
    text = target.read_bytes().decode()
    assert text.count("BEGIN:VEVENT") == 2
    assert "SUMMARY:Ferie (planned)\r\nDTSTART;VALUE=DATE:20261228\r\n" in text
    assert "DTEND;VALUE=DATE:20270106\r\n" in text
    assert "DTSTAMP:20261103T120000Z\r\n" in text
    whole = run(folder, "export", "ics")
    assert whole.count("BEGIN:VEVENT") == 7
    assert "DESCRIPTION:4.00 h a day" in whole


def test_export_needs_year_files(tmp_path: Path) -> None:
    example("fr", tmp_path)
    assert "there are no year files to export yet" in run(tmp_path, "export", "csv", code=1)


def test_a_broken_file_is_reported_without_a_traceback(folder: Path) -> None:
    (folder / "2026.toml").write_text("schema = 2\n", encoding="utf-8")
    error = run(folder, "report", code=1)
    assert error == "error: 2026.toml: schema 2; this version reads files with schema = 1\n"
