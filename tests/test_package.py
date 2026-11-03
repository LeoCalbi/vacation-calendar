from importlib.metadata import version

import vacation_calendar


def test_version_matches_metadata() -> None:
    assert vacation_calendar.__version__ == version("vacation-calendar")
