import streamlit as st
import pandas as pd
from os.path import exists
from datetime import datetime
CALENDAR_PATH = "calendar2023.parquet"
CONFIG_PATH = "config.json"

from vacation_calendar.chore import read_config

st.title("Vacation calendar")


if exists(CALENDAR_PATH):
    df = pd.read_parquet(CALENDAR_PATH)
else:
    st.write("No calendar found, let's create one")
    s = st.button("Create a new calendar")

    if s:
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



# st.write(df)




# start_date = st.date_input("Start Date")
# end_date = st.date_input("End Date")



