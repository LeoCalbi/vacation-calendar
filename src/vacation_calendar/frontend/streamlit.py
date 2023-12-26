import streamlit as st
import pandas as pd
from os.path import exists
from datetime import datetime
from vacation_calendar.chore import create_base_calendar, compute_total_time_off
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
    holidays = df[df.IS_HOLIDAY][["DATE","HOLIDAY"]]
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
    calendar_options = {"editable": True, "selectable": True}
    calendar_events = _generate_weekends_from(df)
    calendar_events += _generate_holidays_view_from(df)
        
    

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
    cal = calendar(options=calendar_options, events=calendar_events, custom_css=custom_css)

    st.write(cal)


generate_calendar_view_from(df)

# start_date = st.date_input("Start Date")
# end_date = st.date_input("End Date")
