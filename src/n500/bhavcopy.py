"""NSE cash-market bhavcopy: download and parse.

Two formats exist:
  * legacy  (until 2024-07-05): content/historical/EQUITIES/YYYY/MON/cmDDMONYYYYbhav.csv.zip
  * UDiFF   (from 2024-07-08):  content/cm/BhavCopy_NSE_CM_0_0_0_YYYYMMDD_F_0000.csv.zip
Both are normalised to one schema (COLUMNS). Turnover is in rupees in both.
"""
from __future__ import annotations

import io
import random
import time
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import requests

BASE = "https://nsearchives.nseindia.com"
UDIFF_START = date(2024, 7, 8)
KEEP_SERIES = ("EQ", "BE", "BZ", "RR")  # normal, trade-to-trade (BE/BZ), REITs (RR)
COLUMNS = ["date", "symbol", "series", "isin", "open", "high", "low", "close",
           "last", "prev_close", "volume", "turnover", "trades"]
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
           "Accept": "*/*", "Referer": "https://www.nseindia.com/"}


def url_for(d: date) -> str:
    if d >= UDIFF_START:
        return f"{BASE}/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
    mon = d.strftime("%b").upper()
    return f"{BASE}/content/historical/EQUITIES/{d:%Y}/{mon}/cm{d:%d}{mon}{d:%Y}bhav.csv.zip"


def raw_path(root: Path, d: date) -> Path:
    return root / f"{d:%Y}" / f"{d:%Y%m%d}.csv.zip"


def download_day(d: date, root: Path, session: requests.Session, retries: int = 4) -> str:
    """Return 'ok', 'cached' or 'holiday'. A 404 means no trading that day."""
    out = raw_path(root, d)
    if out.exists() and out.stat().st_size > 0:
        return "cached"
    for attempt in range(retries):
        try:
            r = session.get(url_for(d), headers=HEADERS, timeout=30)
        except requests.RequestException:
            time.sleep(2 ** attempt + random.random())
            continue
        if r.status_code == 404:
            return "holiday"
        if r.status_code == 200 and r.content[:2] == b"PK":
            out.parent.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".part")
            tmp.write_bytes(r.content)
            tmp.replace(out)
            return "ok"
        time.sleep(2 ** attempt + random.random())  # 403/429/5xx: back off
    raise RuntimeError(f"failed {d} after {retries} attempts")


def _read_zip(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as z:
        name = z.namelist()[0]
        return pd.read_csv(io.BytesIO(z.read(name)), dtype=str)


def parse(path: Path) -> pd.DataFrame:
    df = _read_zip(path)
    df.columns = [c.strip() for c in df.columns]
    if "TckrSymb" in df.columns:  # UDiFF
        df = df[df["FinInstrmTp"].str.strip() == "STK"]
        m = {"TradDt": "date", "TckrSymb": "symbol", "SctySrs": "series", "ISIN": "isin",
             "OpnPric": "open", "HghPric": "high", "LwPric": "low", "ClsPric": "close",
             "LastPric": "last", "PrvsClsgPric": "prev_close", "TtlTradgVol": "volume",
             "TtlTrfVal": "turnover", "TtlNbOfTxsExctd": "trades"}
        df = df.rename(columns=m)
        df["date"] = pd.to_datetime(df["date"].str.strip(), format="%Y-%m-%d")
    else:  # legacy
        m = {"TIMESTAMP": "date", "SYMBOL": "symbol", "SERIES": "series", "ISIN": "isin",
             "OPEN": "open", "HIGH": "high", "LOW": "low", "CLOSE": "close", "LAST": "last",
             "PREVCLOSE": "prev_close", "TOTTRDQTY": "volume", "TOTTRDVAL": "turnover",
             "TOTALTRADES": "trades"}
        df = df.rename(columns=m)
        df["date"] = pd.to_datetime(df["date"].str.strip(), format="mixed", dayfirst=True)
    # the file name is the trading day; the in-file stamp must agree with it
    fdate = pd.Timestamp(path.name[:8])
    bad = df["date"].dropna().ne(fdate)
    if bad.any():
        raise ValueError(f"{path.name}: in-file date {df.loc[bad.index[bad], 'date'].iloc[0]} != file date")
    df["date"] = fdate
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = None
    df = df[COLUMNS].copy()
    for c in ("symbol", "series", "isin"):
        df[c] = df[c].astype("string").str.strip()
    df = df[df["series"].isin(KEEP_SERIES)]
    for c in ("open", "high", "low", "close", "last", "prev_close", "turnover"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("float64")
    for c in ("volume", "trades"):
        df[c] = pd.to_numeric(df[c], errors="coerce").astype("Int64")
    return df.reset_index(drop=True)
