from os.path import exists
from enum import Enum
import json
import pandas as pd
import numpy as np
import datetime
import holidays

# NOTE: the following lines will be taken from settings
CONFIG_PATH = "config.json" 
current_year = datetime.datetime.now().year
country = "IT"
subdiv = "BO"
WORKING_HOURS = 8

class TimeOffType(Enum):
    VAC = "VACATION"
    ROL = "PTF" # Paid Time Off

def read_config():
    if not exists(CONFIG_PATH):
        d = {}
    else:
        with open(CONFIG_PATH, "r") as f:
            d = json.load(f)
    return d


def create_base_calendar():
    year_holidays = holidays.country_holidays(country, years=current_year, subdiv=subdiv)
    start= datetime.datetime(current_year,month=1,day=1)
    end = datetime.datetime(current_year,month=12,day=31)
    dates = pd.date_range(start=start, end=end, freq="D")
    dates_df = dates.to_frame(index=False, name="DATE")
    dates_df["MONTH"] = dates_df.DATE.apply(lambda ser: ser.month)
    dates_df["IS_WEEKEND"] = dates_df["DATE"].dt.dayofweek.isin([5, 6])
    dates_df["HOLIDAY"] = (
        dates_df["DATE"]
        .apply(
            lambda day: year_holidays[day] if day in year_holidays else pd.NA,
        )
        .astype("category")
    )
    dates_df["IS_HOLIDAY"] = np.where(dates_df["HOLIDAY"].notna(),True, False)
    dates_df[TimeOffType.VAC] = 0.
    dates_df[TimeOffType.ROL] = 0.
    return dates_df


def add_to_calendar(df: pd.DataFrame, date:"datetime.datetime", value:float, col:str):

    mask = df.DATE == date

    if df[mask].empty:
        raise ValueError(f"The chosen date '{date}' does not exist in the calendar")
    if (df[mask][["IS_WEEKEND","IS_HOLIDAY"]]).any().any():
        raise ValueError("The chosen day is a weekend or holiday")
    if value > WORKING_HOURS:
        raise ValueError("The amount per day cannot be higher than the working hours.")
    df.loc[mask, col] = value
    return df



    