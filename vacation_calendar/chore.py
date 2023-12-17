from os.path import exists
from enum import Enum
import json
import pandas as pd
import numpy as np
import datetime
import holidays
from typing import Union
from functools import singledispatch

# NOTE: the following lines will be taken from settings
CONFIG_PATH = "config.json"
current_year = datetime.datetime.now().year
country = "IT"
subdiv = "BO"
WORKING_HOURS = 8


class TimeOffType(Enum):
    VAC = "VACATION"
    ROL = "PTF"  # Paid Time Off


def read_config():
    # NOTE: this will be probably removed using pydantic
    if not exists(CONFIG_PATH):
        d = {}
    else:
        with open(CONFIG_PATH, "r") as f:
            d = json.load(f)
    return d


def create_base_calendar() -> pd.DataFrame:
    """Generate the base calendar, with holidays and weekends corresponding to the config files
    Returns:
        (pd.DataFrame): the calendar dataframe
    """
    year_holidays = holidays.country_holidays(country, years=current_year, subdiv=subdiv)
    start = datetime.datetime(current_year, month=1, day=1)
    end = datetime.datetime(current_year, month=12, day=31)
    dates = pd.date_range(start=start, end=end, freq="D")
    dates_df = dates.to_frame(index=False, name="DATE")
    dates_df["MONTH"] = dates_df.DATE.apply(lambda ser: ser.month)
    dates_df["YEAR"] = dates_df.DATE.apply(lambda ser: ser.year)
    dates_df["IS_WEEKEND"] = dates_df["DATE"].dt.dayofweek.isin([5, 6])
    dates_df["HOLIDAY"] = (
        dates_df["DATE"]
        .apply(
            lambda day: year_holidays[day] if day in year_holidays else pd.NA,
        )
        .astype("category")
    )
    dates_df["IS_HOLIDAY"] = np.where(dates_df["HOLIDAY"].notna(), True, False)
    dates_df[TimeOffType.VAC] = 0.0
    dates_df[TimeOffType.ROL] = 0.0
    return dates_df


@singledispatch
def add_to_calendar(
    date: Union[datetime.date, str], df: pd.DataFrame, value: float, ttype: TimeOffType
) -> pd.DataFrame:
    """
    Add the input value to the calendar, at the passed date.
    Args:
        date (Union[datetime.date, str]): date in which the time off will be taken
        df (pd.DataFrame): input calendar df
        value (float): amount of time off hours. Canno be higher than the `WORKING_HOURS`
        ttype (TimeOffType): the type of time off; ROL for PaidTimeOff, VAC for Vacation.
    Returns:
        (pd.DataFrame): the df with the added time off
    Raises: 
        ValueError if:
        - `value` is higher than the `WORKING_HOURS`
        - the chosen date is not present in the calendar
        - the chosen date is a weekend or a holiday
    """
    if value > WORKING_HOURS:
        raise ValueError("The amount per day cannot be higher than the working hours.")

    mask = df.DATE == date

    if df[mask].empty:
        raise ValueError(f"The chosen date '{date}' does not exist in the calendar")

    if (df[mask][["IS_WEEKEND", "IS_HOLIDAY"]]).any().any():
        raise ValueError("The chosen day is a weekend or holiday")

    df.loc[mask, ttype] = value
    return df


@add_to_calendar.register
def add_range_to_calendar(date: list, df: pd.DataFrame, value: float, ttype: TimeOffType) -> pd.DataFrame:
    """
    Add the input value to the calendar, in the passed date range. If weekends or holidays fall in the range,
    they will be removed. 
    Args:
        date (Union[List[datetime.date], List[str]]): list containing the start and the end dates in which 
            the time off will be taken
        df (pd.DataFrame): input calendar df
        value (float): amount of time off hours. Canno be higher than the `WORKING_HOURS`
        ttype (TimeOffType): the type of time off; ROL for PaidTimeOff, VAC for Vacation.
    Returns:
        (pd.DataFrame): the df with the added time off
    Raises: 
        ValueError if:
        - the start date is after the end date
        - `value` is higher than the `WORKING_HOURS`
        - the chosen dates is not present in the calendar
    """
    start_date = date[0]
    end_date = date[1]
    if start_date > end_date:
        raise ValueError(
            f"The start date cannot be after the end date. Start: f{start_date}, End: {end_date}"
        )

    if value > WORKING_HOURS:
        raise ValueError("The amount per day cannot be higher than the working hours.")

    mask = (df.DATE >= start_date) & (df.DATE <= end_date)
    mask &= ~df[mask][["IS_HOLIDAY", "IS_WEEKEND"]].any(axis=1)
    if df[mask].empty:
        raise ValueError(f"The chosen dates range'{date}' does not exist in the calendar")

    df.loc[mask, ttype] = value
    return df


def compute_total_time_off(df: pd.DataFrame) -> pd.DataFrame:
    """
    Groups the dataframe my month computing the sum
    Args:
        df (pd.Dataframe): the calendar dataframe
    Returns:
        (pd.DataFrame): a grouped dataframe with the sum of the time off days
    """
    mask = df.YEAR == current_year
    group = df[mask][["MONTH",TimeOffType.ROL,TimeOffType.VAC]].groupby("MONTH").sum().reset_index()
    return group