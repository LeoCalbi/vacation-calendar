# vacation-calendar

[![CI](https://github.com/LeoCalbi/vacation-calendar/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/LeoCalbi/vacation-calendar/actions/workflows/ci.yml) ![Python 3.13 | 3.14 | 3.15](https://img.shields.io/badge/python-3.13%20%7C%203.14%20%7C%203.15-blue) [![License: MIT](https://img.shields.io/github/license/LeoCalbi/vacation-calendar)](LICENSE) [![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff) [![types: mypy strict](https://img.shields.io/badge/types-mypy%20strict-blue)](https://mypy-lang.org) [![Pixi](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/prefix-dev/pixi/main/assets/badge/v0.json)](https://pixi.sh)

Plan work time off across years. vacation-calendar keeps a balance for each kind of leave you have (vacation, reduced hours, floating holidays), adds what you accrue each month or year, subtracts the leaves you take or plan, and checks the result against the balances your payslip prints. Public holidays come from the [`holidays`](https://github.com/vacanza/holidays) library, which covers most countries and many of their regions, and you can add company holidays and office closures on top.

It is a command-line tool and a Python library. Your data lives in small TOML files you can read, edit and keep in git.

## What it handles

- **Buckets** of leave, each counted in hours, working days or calendar days.
- **Accrual** per month, or granted once a year on a date; rates change over time, so a raise in seniority or a new contract does not rewrite past balances.
- **Carry-over** from one year to the next, computed or taken from the payslip.
- **Leaves** on a day or a range, planned or taken; rest days and holidays inside a range use nothing.
- **Payslip checkpoints** that either check the computed balance (and show the difference) or override it from that month on.
- **Holidays** from a country, a region or a stock exchange, plus your own additions and removals by date, by name or by a recurrence rule.
- **Closures**, days the office is shut and the hours come out of a bucket, such as a summer shutdown.
- **Make-up working days**, weekends that become working days (as in China), and any weekly schedule, short Fridays included.

Not handled yet: expiry rules (leave that lapses after a deadline is tracked by year of accrual, but nothing expires), part-time accrual pauses, and choosing a bucket automatically.

## Install

vacation-calendar needs Python 3.13 or newer. It is not on PyPI yet; install it from GitHub. With [uv](https://docs.astral.sh/uv/), the command gets an environment of its own:

```sh
uv tool install git+https://github.com/LeoCalbi/vacation-calendar
```

To try it without installing, run `uvx --from git+https://github.com/LeoCalbi/vacation-calendar vacation-calendar --help`. With pip, in an environment of your choice:

```sh
pip install git+https://github.com/LeoCalbi/vacation-calendar
```

## Quickstart

Point the tool at a data folder (by default it uses your user data folder, such as `~/.local/share/vacation-calendar` on Linux), then start from one of the examples:

```bash
export VACATION_CALENDAR_DIR="$PWD/my-time-off"
vacation-calendar init
vacation-calendar init it-bo --sample
```

`init` without an argument lists the examples; `--sample` also copies a year of sample leaves. Edit `config.toml` to match your contract, then look at the year:

```bash
vacation-calendar holidays 2026
vacation-calendar report 2026
```

Plan a leave, record it as taken once it happened, and check a payslip:

```bash
vacation-calendar add 2026-11-16 2026-11-18 --bucket vacation
vacation-calendar add 2026-11-19 --bucket rol --hours 4
vacation-calendar take 2026-11-17
vacation-calendar payslip 2026-10 --balance vacation=79.80 --balance rol=104.87
vacation-calendar report 2026-09..2026-12
```

`add` takes a day or a first and last day; without `--hours` each working day is taken in full. It files the leave under the year it starts in, checks every year file before it writes, and prints the bucket's balance at the end of that year. `--bucket` can be left out when there is only one bucket. `payslip` prints, for each bucket, the payslip's balance next to the computed one; with `--override` the payslip's balances replace the computed ones from that month on.

Export the balances or the leaves:

```bash
vacation-calendar export csv 2026 --output balances.csv
vacation-calendar export ics --output leaves.ics
```

The ICS file holds one all-day event per leave, for import into any calendar app. Every command takes `--data-dir` in place of the environment variable, and `--help`.

## The report

`vacation-calendar report` shows one table per bucket, month by month (by default the current year; `2026..2027` or `2026-03..2026-12` for a range), in the bucket's unit with two decimals:

| Column | Meaning |
| - | - |
| Open | the balance at the start of the month |
| Accrued | the monthly accrual, plus any yearly grant that falls in the month |
| Taken, Planned | leave recorded as taken, or still planned |
| Closures | closure days that used this bucket (shown only when there are some) |
| Adjust | a carry-over that differs from the computed one, or a payslip override (shown only when there is one) |
| Close | Open + Accrued − Taken − Planned − Closures + Adjust |
| Days | an hours bucket's closing balance in days of the schedule's usual length |
| Payslip, Delta | a payslip's balance and its difference from the computed one |

`--lots` adds the balance split by the year it accrued in; leave uses the oldest year first. `--strict` fails when a balance goes below zero. Below the tables come the notices: a planned leave that has already passed, a payslip that differs from the computed balance, a negative balance, a leave that uses nothing, a first year without a carry-over.

## The data folder

The folder holds `config.toml`, the settings shared by every year, and one file per year (`2026.toml`) with that year's leaves and payslips. Both start with `schema = 1`, the file-format version. Dates are TOML dates (`2026-08-17`, not quoted).

### `config.toml`

```toml
schema = 1

[holidays]
base = { country = "IT", subdiv = "BO" }  # or { market = "XNYS" }, or {} for no public holidays

[[holidays.add]]  # a company holiday: a day off that uses no leave
name = "Christmas Eve"
rule = "FREQ=YEARLY;BYMONTH=12;BYMONTHDAY=24"

[[holidays.remove]]  # a public holiday you work on, by name or by date
name = "Santo Stefano"

[[closures]]  # the office is closed and the hours come from a bucket
name = "Summer shutdown"
start = 2026-08-10
end = 2026-08-14
bucket = "vacation"

[[schedule]]  # hours per weekday; a new entry from a later date replaces it
valid_from = 2025-01-01
hours = { mon = 8, tue = 8, wed = 8, thu = 8, fri = 8 }

[buckets.vacation]
name = "Vacation"
unit = "hours"  # hours, days (working days) or calendar_days
accrual = [{ valid_from = 2025-01-01, monthly = 13.33 }]

[buckets.rol]
name = "Reduced working hours"
accrual = [{ valid_from = 2025-01-01, monthly = 8.67 }]
```

- `base` picks the public holidays: a `country` with an optional `subdiv` (region), or a financial `market`. Names are in the country's own language unless you set `language` (such as `"en_US"`); run `vacation-calendar holidays` to see them, since removals by name must match them exactly.
- Holiday additions and closures fall on one `date`, on a `start`-`end` range, or on a `rule`: an iCalendar recurrence rule (RFC 5545 RRULE) such as `FREQ=YEARLY;BYMONTH=8;BYMONTHDAY=14`, with `BYEASTER=1` for days relative to Easter. Additions, removals and closures take optional `valid_from` and `valid_until` dates.
- `[[extra_workdays]]` turns rest days into working days (`date`, range or `rule`, with optional `hours`); make-up days that the base already knows, as in China, are included without it.
- A bucket's `accrual` is a list of dated entries: `monthly` is credited each month from `valid_from`, which must be the 1st; `yearly` is granted each year on the month and day of `valid_from`. Each entry applies until the next one starts, so seniority steps are entries with later dates.
- Amounts have at most two decimals, like a payslip.

### `YYYY.toml`

```toml
schema = 1

[carry_over]  # optional: the balances on 1 January; otherwise computed from the year before
vacation = 25.5
rol = 20.3

[[leaves]]
start = 2026-08-17
end = 2026-08-21
bucket = "vacation"
state = "planned"  # or taken

[[leaves]]
date = 2026-03-06
hours = 4  # per working day; leave it out for whole days
bucket = "rol"
state = "taken"

[[payslips]]
month = "2026-09"  # quoted: TOML has no year-month type
mode = "check"  # or override
balances = { vacation = 61.2, rol = 40.1 }  # the closing balances the payslip prints
```

- A leave belongs to the file of the year it starts in and may end in the next year, so a Christmas break stays one entry.
- `hours` are always hours, also for a days bucket: a 4-hour leave on an 8-hour day uses half a day. A calendar-days bucket counts every day of the range, weekends and holidays included, and takes no `hours`.
- A carry-over given for a later year is treated as a correction: the report shows it in Adjust.
- The commands keep your comments when they edit a file.

## Examples

Each example is a `config.toml` with a sample `2026.toml`; `vacation-calendar init` copies one. Their comments cite the law each number comes from, or say that it is illustrative. Check your own contract: these are starting points, not legal advice.

| Example | Shows |
| - | - |
| `it-bo` (Bologna, Italy) | hours buckets with monthly accrual (CCNL Metalmeccanici), a summer closure |
| `de-by` (Bavaria, Germany) | a days bucket granted each January, a Christmas closure |
| `fr` (France) | monthly accrual in days from a June reference period, RTT days |
| `us` (United States) | PTO accrued monthly, a floating holiday, a company holiday by rule, federal holidays removed |
| `gb-eng` (England) | a leave year that starts in April, bank holidays with substitute days |
| `es-md` (Madrid, Spain) | regional holidays, a yearly grant used in full |
| `jp` (Japan) | grants that grow with seniority, year-end company holidays |
| `cn` (China) | make-up working days on weekends, a higher grant from a later year |

## Use as a library

```python
import datetime as dt
from pathlib import Path

from vacation_calendar.ledger import build_ledger
from vacation_calendar.storage import check_folder

config, plans = check_folder(Path("my-time-off"))
ledger = build_ledger(config, plans, today=dt.date.today())
for row in ledger.rows["vacation"]:
    print(row.month, row.closing)
```

`check_folder` reads and validates the folder; `build_ledger` returns each bucket's rows and the notices. The models in `vacation_calendar.models` can also be built in code, without files.

## Development

The project uses [pixi](https://pixi.sh) for its environments:

```sh
pixi run check                    # format check, lint, type check and tests
pixi run fmt                      # format and fix what ruff can
pixi run -e py313 test            # the tests on Python 3.13 (also py314, py315)
```

## License

MIT, see [LICENSE](LICENSE). Authors: Leonardo Calbi and Marcello De Giorgi.
