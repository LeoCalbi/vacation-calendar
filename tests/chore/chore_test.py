from vacation_calendar.chore import create_base_calendar, add_to_calendar, TimeOffType


def test_create_base_calendar(monkeypatch):
    #TODO: patch settings when ready
    monkeypatch.setattr("vacation_calendar.chore.current_year",2023)
    monkeypatch.setattr("vacation_calendar.chore.country","IT")

    df = create_base_calendar()
    df["YEAR"] = df.DATE.apply(lambda ser: ser.year)
    assert all(df.YEAR.unique() == [2023])
    assert df[df.DATE == "2023-12-16"].WEEKEND.iloc[0]
    assert df[df.HOLIDAY == "Pasqua di Resurrezione"].DATE.iloc[0].date().__str__() == "2023-04-09"
    assert df[TimeOffType.ROL].sum() == 0
    assert df[TimeOffType.VAC].sum() == 0

    monkeypatch.setattr("vacation_calendar.chore.current_year",2022)

    df = create_base_calendar()
    
    assert "2023-12-16" not in df.DATE.values
    assert not df[df.DATE == "2022-12-16"].WEEKEND.iloc[0]
    assert df[df.DATE == "2022-12-17"].WEEKEND.iloc[0]
    assert not df[df.HOLIDAY == "Pasqua di Resurrezione"].DATE.iloc[0].date().__str__() == "2022-04-09"


def test_add_to_calendar(monkeypatch):
    #TODO: patch settings when ready
    monkeypatch.setattr("vacation_calendar.chore.current_year",2023)
    monkeypatch.setattr("vacation_calendar.chore.country","IT")
    df = create_base_calendar()
    df = add_to_calendar(df, "2023-12-15", 3,TimeOffType.ROL)
    assert df[df.DATE == "2023-12-15"][TimeOffType.ROL].iloc[0] == 3
    # to be tested:
    # simple add
    # over adding (more than 8 days)
    # add in a weekend/holiday
    
