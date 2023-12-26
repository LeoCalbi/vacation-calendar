import streamlit as st
import pandas as pd
from os.path import exists
from datetime import datetime, date
from vacation_calendar.chore import create_base_calendar, compute_total_time_off, add_to_calendar
from streamlit_calendar import calendar

CALENDAR_PATH = "calendar2023.parquet"
CONFIG_PATH = "config.json"

from vacation_calendar.chore import read_config

st.title("Vacation calendar")


if exists(CALENDAR_PATH):
    df = pd.read_parquet(CALENDAR_PATH)
else:
    st.write("No calendar found, let's create one")
    st.session_state.create_cal = st.button("Create a new calendar")

    if "create_cal" in st.session_state:
        d = read_config()
        current_year = datetime.now().year
        if current_year in d:
            d = d[current_year]
            st.write("Are these data correct?")
            st.write(d)
            # TODO
        else:
            d[current_year] = {}
            country = st.text_input("Country")
            pvc = st.number_input("Previous year vacation")
            pvr = st.number_input("Prevuous year ROL")
            df = create_base_calendar()
            df.to_parquet(CALENDAR_PATH)


def _generate_weekends_from(df) -> list:
    we_dates = df[(df.IS_WEEKEND) & (~df.IS_HOLIDAY)]["DATE"]
    we_list = [
        {
            "start": str(date),
            "end": str(date),
            "allDay": True,
            "className": "weekend",
            "display": "background",
            "editable": False,
            "backgroundColor": "rgba(13,111,166,255)",
        }
        for date in we_dates.values
    ]
    return we_list


def _generate_holidays_view_from(df):
    holidays = df[df.IS_HOLIDAY][["DATE", "HOLIDAY"]]
    holidays_list = [
        {
            "start": str(date.DATE),
            "end": str(date.DATE),
            "title": date.HOLIDAY,
            "allDay": True,
            "className": "holiday",
            "display": "background",
            "editable": False,
            "backgroundColor": "#f6c064",
        }
        for _, date in holidays.iterrows()
    ]
    return holidays_list


def generate_calendar_view_from(df):
    start = str(date(date.today().year, 1, 1))
    end = str(date(date.today().year, 12, 31))
    calendar_events = _generate_weekends_from(df)
    calendar_events += _generate_holidays_view_from(df)

    calendar_options = {
        "editable": True,
        "selectable": True,
        "validRange": {"start": start, "end": end},
    }
    custom_css = """
        .fc-event-past {
            opacity: 0.8;
        }
        .weekend {
            opacity: 0.2;
        }
        .holiday {
            opacity: 1
        }
        .fc-event-time {
            font-style: italic;
        }
        .fc-event-title {
            font-weight: 700;
        }
        .fc-toolbar-title {
            font-size: 2rem;
        }
    """
    calendar(options=calendar_options, events=calendar_events, custom_css=custom_css)


generate_calendar_view_from(df)

st.header("Add vacations of PTO")
col1, col2 = st.columns(2)
col3, col4 = st.columns(2)

with col1:
    start_date = datetime.strftime(col1.date_input("Start Date"), "%Y-%m-%d")
with col2:
    end_date = datetime.strftime(col2.date_input("End Date"), "%Y-%m-%d")
with col3:
    amount_hour = col3.number_input(
        "Amount of hours",
        0,
        8,
        help="Amount of hours of PTO or VAC per day. "
        "In case start and end dates are different, the same amount of hours is repeated for each day.",
    )
with col4:
    amount_type = col4.radio("Type", ["VAC", "PTO"])

s = st.button("Save")
if s:
    dates = start_date if start_date == end_date else [start_date, end_date]
    df = add_to_calendar(dates, df, amount_hour, amount_type)
