"""Bring the price database up to date: fetch any bhavcopies after the last stored day."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import requests

from .bhavcopy import download_day, parse, raw_path
from .db import ROOT

RAW = ROOT / "data" / "raw" / "bhav"


def update_prices(con, today: date | None = None) -> list[date]:
    today = today or date.today()
    last = con.execute("SELECT max(date) FROM bhav").fetchone()[0]
    s, new = requests.Session(), []
    d = last + timedelta(days=1)
    while d <= today:                      # weekends included: Muhurat / special sessions
        st = download_day(d, RAW, s, retries=2)  # a 404 (holiday / not yet published) is skipped,
        if st in ("ok", "cached"):              # and retried on the next refresh
            new.append(d)
        d += timedelta(days=1)
    if new:
        df = pd.concat([parse(raw_path(RAW, x)) for x in new], ignore_index=True)
        df = df.sort_values("volume", na_position="first").drop_duplicates(["date", "symbol", "series"], keep="last")
        con.execute("DELETE FROM bhav WHERE date >= ?", [min(new)])
        con.execute("INSERT INTO bhav SELECT * FROM df")
    return new
