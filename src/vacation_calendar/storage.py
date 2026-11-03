"""The data folder: `config.toml` and one `YYYY.toml` per year, edited with their comments kept.

`config.toml` holds the settings shared by every year; its `holidays`, `closures`, `schedule`
and `extra_workdays` make up the work calendar. A year file holds that year's carry-over, leaves
and payslips; its year comes from the file name. Both start with `schema = 1`, the file-format
version.
"""

import datetime as dt
import os
import re
import shutil
from collections.abc import Callable
from decimal import Decimal
from importlib.resources import files
from importlib.resources.abc import Traversable
from pathlib import Path

import platformdirs
import tomlkit
from pydantic import ValidationError
from tomlkit.exceptions import ParseError
from tomlkit.items import AoT, Comment, InlineTable, Table, Whitespace
from tomlkit.toml_document import TOMLDocument

from vacation_calendar.holiday_set import CountryBase, MarketBase
from vacation_calendar.models import (
    Config,
    Leave,
    LeaveState,
    PayslipEntry,
    YearPlan,
    check_plans,
)
from vacation_calendar.work_calendar import WorkCalendar

SCHEMA = 1
"""The file-format version this library reads and writes."""

ENV_VAR = "VACATION_CALENDAR_DIR"
"""The environment variable that points to the data folder."""

CONFIG_FILE = "config.toml"

Location = tuple[int | str, ...]

_YEAR_FILE = re.compile(r"(\d{4})\.toml")
_HEADER = re.compile(r"\s*\[\[?[\w.\" -]+\]\]?\s*(#.*)?$")
_CALENDAR_KEYS = ("closures", "schedule", "extra_workdays")
_HOLIDAY_KEYS = ("base", "add", "remove")


class StorageError(ValueError):
    """A data file that cannot be read or does not fit the format; `problems` lists why."""

    def __init__(self, path: Path, problems: list[str]) -> None:
        """Create the error for `path` from one message per problem."""
        self.path = path
        self.problems = tuple(problems)
        super().__init__("\n".join(f"{path.name}: {problem}" for problem in problems))


def data_dir(override: Path | None = None) -> Path:
    """Return the data folder: `override`, else `$VACATION_CALENDAR_DIR`, else the user data dir."""
    if override is not None:
        return override
    if os.environ.get(ENV_VAR):
        return Path(os.environ[ENV_VAR])
    return platformdirs.user_data_path("vacation-calendar", appauthor=False)


def read_config(folder: Path) -> Config:
    """Read and validate `config.toml` in `folder`."""
    path = folder / CONFIG_FILE
    data = _read(path).unwrap()
    del data["schema"]
    holidays = data.pop("holidays", {})
    if not isinstance(holidays, dict):
        raise StorageError(path, ["holidays: must be a table"])
    problems: list[str] = []
    base = holidays.get("base")
    if isinstance(base, dict):
        # Validated on its own so its errors name one kind of base, not both.
        kind = MarketBase if "market" in base else CountryBase
        try:
            holidays["base"] = kind.model_validate(base) if base else None
        except ValidationError as error:
            problems += _problems(error, lambda location: ("holidays", "base", *location))
            holidays["base"] = None
    calendar = holidays | {key: data.pop(key) for key in _CALENDAR_KEYS if key in data}
    try:
        config = Config.model_validate({"calendar": calendar, **data})
    except ValidationError as error:
        problems += _problems(error, _config_location)
    if problems:
        raise StorageError(path, problems)
    return config


def read_plans(folder: Path) -> list[YearPlan]:
    """Read and validate every `YYYY.toml` in `folder`, in year order."""
    return [YearFile.open(folder, year).plan() for year in year_files(folder)]


def year_files(folder: Path) -> list[int]:
    """Return the years that have a file in `folder`, in order."""
    found = (_YEAR_FILE.fullmatch(path.name) for path in folder.glob("*.toml"))
    return sorted(int(match[1]) for match in found if match)


def check_folder(folder: Path, edited: "YearFile | None" = None) -> tuple[Config, list[YearPlan]]:
    """Read the configuration and every year plan, then check them together.

    `edited` stands in for its year's file on disk, so an edit can be checked before it is saved.
    Raises `StorageError` for a file that does not fit the format, `PlanError` for plans that do
    not fit the configuration.
    """
    config = read_config(folder)
    plans = {plan.year: plan for plan in read_plans(folder)}
    if edited is not None:
        plans[edited.year] = edited.plan()
    ordered = [plans[year] for year in sorted(plans)]
    check_plans(ordered, config.buckets, WorkCalendar(config.calendar))
    return config, ordered


class YearFile:
    """One year file, kept as a TOML document so edits keep its comments and layout."""

    def __init__(self, path: Path, year: int, document: TOMLDocument) -> None:
        """Wrap `document`, the contents of `path` for `year`."""
        self.path = path
        self.year = year
        self.document = document

    @classmethod
    def open(cls, folder: Path, year: int) -> "YearFile":
        """Open the file of `year` in `folder`, or start an empty one if it does not exist."""
        path = folder / f"{year}.toml"
        if path.exists():
            return cls(path, year, _read(path))
        document = tomlkit.document()
        document["schema"] = SCHEMA
        return cls(path, year, document)

    def plan(self) -> YearPlan:
        """Validate the document and return it as a year plan."""
        data = self.document.unwrap()
        data.pop("schema", None)  # absent in a new file
        if "year" in data:
            raise StorageError(self.path, ["year: comes from the file name; remove it"])
        try:
            return YearPlan.model_validate({"year": self.year, **data})
        except ValidationError as error:
            raise StorageError(self.path, _problems(error, lambda location: location)) from None

    def add_leave(self, leave: Leave) -> None:
        """Add `leave`, placed among the others by its first day."""
        table = tomlkit.table()
        if leave.date is not None:
            table["date"] = leave.date
        else:
            table["start"] = leave.first
            table["end"] = leave.last
        if leave.hours is not None:
            table["hours"] = _number(leave.hours)
        table["bucket"] = leave.bucket
        table["state"] = leave.state.value
        leaves = [Leave.model_validate(item) for item in self._array("leaves").unwrap()]
        position = next(
            (index for index, other in enumerate(leaves) if other.first > leave.first), len(leaves)
        )
        self._insert("leaves", position, table)

    def take(self, day: dt.date, bucket: str | None = None) -> list[Leave]:
        """Mark as taken every planned leave that includes `day` (from `bucket`, if given).

        Returns the leaves it changed, as they were before.
        """
        changed = []
        for table in self._array("leaves"):
            leave = Leave.model_validate(table.unwrap())
            if (
                leave.state is LeaveState.PLANNED
                and leave.first <= day <= leave.last
                and bucket in {None, leave.bucket}
            ):
                table["state"] = LeaveState.TAKEN.value
                changed.append(leave)
        return changed

    def set_payslip(self, entry: PayslipEntry) -> None:
        """Add the payslip of `entry.month`, replacing the one already there for that month."""
        table = tomlkit.table()
        table["month"] = f"{entry.month:%Y-%m}"
        table["mode"] = entry.mode.value
        balances = tomlkit.inline_table()
        balances.update({key: _number(value) for key, value in entry.balances.items()})
        table["balances"] = balances
        payslips = self._array("payslips")
        months = [PayslipEntry.model_validate(item).month for item in payslips.unwrap()]
        if entry.month in months:
            _replace_values(payslips[months.index(entry.month)], table)
            return
        position = next(
            (index for index, month in enumerate(months) if month > entry.month), len(months)
        )
        self._insert("payslips", position, table)

    def save(self) -> None:
        """Write the document to its file, replacing it in one step.

        Blank lines are tidied: one before each table header that follows a value, none doubled.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(_tidy(tomlkit.dumps(self.document)), encoding="utf-8")
        temporary.replace(self.path)

    def _array(self, key: str) -> AoT:
        found = self.document.get(key)
        if found is None:
            return AoT([])
        if not isinstance(found, AoT):
            raise StorageError(self.path, [f"{key}: must be an array of tables, [[{key}]]"])
        return found

    def _insert(self, key: str, position: int, table: Table) -> None:
        array = self.document.get(key)
        if not isinstance(array, AoT):
            self.document[key] = tomlkit.aot()
            array = self.document[key]
        if position > 0:
            # Comments at the end of the table before belong to the header after it.
            body = array[position - 1].value.body
            notes: list[Comment | Whitespace] = []
            while body and body[-1][0] is None and isinstance(body[-1][1], Comment | Whitespace):
                notes.insert(0, body.pop()[1])  # type: ignore[arg-type]
            for note in notes:
                table.add(note)
        array.insert(position, table)


def example_names() -> list[str]:
    """Return the names of the example data folders shipped with the package."""
    return sorted(entry.name for entry in _examples().iterdir() if entry.is_dir())


def copy_example(name: str, folder: Path, *, with_years: bool = False) -> list[Path]:
    """Copy the example `name` into `folder`: its config, and its year files if `with_years`.

    Refuses to overwrite a file that exists. Returns the files written.
    """
    if name not in example_names():
        msg = f"no example {name!r}; examples: {', '.join(example_names())}"
        raise ValueError(msg)
    sources = [
        entry
        for entry in _examples().joinpath(name).iterdir()
        if entry.name == CONFIG_FILE or (with_years and _YEAR_FILE.fullmatch(entry.name))
    ]
    targets = [folder / source.name for source in sources]
    existing = [target.name for target in targets if target.exists()]
    if existing:
        msg = f"{folder} already has {', '.join(sorted(existing))}; not overwriting"
        raise FileExistsError(msg)
    folder.mkdir(parents=True, exist_ok=True)
    for source, target in zip(sources, targets, strict=True):
        with source.open("rb") as reader, target.open("wb") as writer:
            shutil.copyfileobj(reader, writer)
    return sorted(targets)


def _examples() -> Traversable:
    return files("vacation_calendar").joinpath("examples")


def _read(path: Path) -> TOMLDocument:
    try:
        document = tomlkit.parse(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StorageError(path, [f"not found in {path.parent}"]) from None
    except ParseError as error:
        raise StorageError(path, [f"not valid TOML: {error}"]) from None
    schema = document.get("schema")
    if schema != SCHEMA:
        found = "no schema" if schema is None else f"schema {schema!r}"
        raise StorageError(path, [f"{found}; this version reads files with schema = {SCHEMA}"])
    return document


def _replace_values(target: Table | InlineTable, source: Table | InlineTable) -> None:
    """Make `target` hold the values of `source`, keeping its layout and comments."""
    for key in [key for key in target if key not in source]:
        del target[key]
    for key, value in source.items():
        current = target.get(key)
        if isinstance(current, InlineTable) and isinstance(value, InlineTable):
            _replace_values(current, value)
        else:
            target[key] = value


def _tidy(text: str) -> str:
    """Put a blank line before each table header that follows a value; drop doubled blanks."""
    lines: list[str] = []
    for line in text.rstrip("\n").split("\n"):
        before = lines[-1].strip() if lines else ""
        if not line.strip() and not before:
            continue
        if _HEADER.match(line) and before and not before.startswith("#"):
            lines.append("")
        lines.append(line)
    return "\n".join(lines) + "\n"


def _number(value: Decimal) -> int | float:
    """Return `value` as TOML writes it: an integer when whole, else a float."""
    return int(value) if value == value.to_integral_value() else float(value)


def _config_location(location: Location) -> Location:
    """Map a location in `Config` back to where it is written in `config.toml`."""
    if location[:1] != ("calendar",):
        return location
    inner = location[1:]
    return ("holidays", *inner) if inner[:1] and inner[0] in _HOLIDAY_KEYS else inner


def _problems(error: ValidationError, locate: Callable[[Location], Location]) -> list[str]:
    """Return one message per error, leaving out a list "too short" only because an item failed."""
    errors = error.errors()
    places = [tuple(item["loc"]) for item in errors]
    return [
        f"{_where(locate(place))}{item['msg'].removeprefix('Value error, ')}"
        for item, place in zip(errors, places, strict=True)
        if not (
            item["type"] == "too_short"
            and any(other[: len(place)] == place != other for other in places)
        )
    ]


def _where(location: Location) -> str:
    """Name a location the way the file shows it: `leaves #2.bucket` for the second leave."""
    parts: list[str] = []
    for part in location:
        if isinstance(part, int):
            parts[-1] += f" #{part + 1}"
        else:
            parts.append(part)
    return f"{'.'.join(parts)}: " if parts else ""
