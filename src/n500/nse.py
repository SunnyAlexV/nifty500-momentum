"""Small NSE / niftyindices clients: current Nifty 500 list and split/bonus actions."""
from __future__ import annotations

import re
from datetime import date
from io import StringIO

import pandas as pd
import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36", "Accept": "*/*"}


def nifty500_constituents() -> pd.DataFrame:
    r = requests.get("https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
                     headers=UA, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(StringIO(r.text))
    df.columns = ["company", "industry", "symbol", "series", "isin"]
    return df.apply(lambda s: s.str.strip())


def _factor(subject: str) -> float | None:
    """Price-divisor implied by a corporate action, or None if it doesn't change share count.

    Bonus a:b      -> a new shares for every b held -> price / ((a+b)/b)
    Split F1 -> F2 -> price / (F1/F2)     (consolidation gives a factor < 1)
    """
    s = subject.lower()
    if any(k in s for k in ("ncrps", "preference", "debenture", "ncd")):
        return None  # bonus in preference shares / debt: equity share count unchanged
    m = re.search(r"bonus\s*(\d+)\s*:\s*(\d+)", s)
    if m:
        a, b = map(int, m.groups())
        return (a + b) / b
    if "split" in s or "sub-division" in s or "consolidation" in s:
        fv = re.findall(r"(?:rs|re)\.?\s*(\d+(?:\.\d+)?)", s)
        if len(fv) >= 2:
            f1, f2 = float(fv[0]), float(fv[1])
            if f1 > 0 and f2 > 0 and f1 != f2:
                return f1 / f2
    return None


def split_bonus_actions(start: date, end: date) -> pd.DataFrame:
    """columns: symbol, isin, ex_date, factor, subject, kind.

    kind = 'split_bonus' (factor = share-count multiplier) or 'demerger' (factor = NaN)."""
    s = requests.Session()
    h = dict(UA, Referer="https://www.nseindia.com/companies-listing/corporate-filings-actions")
    s.get("https://www.nseindia.com/", headers=h, timeout=30)
    r = s.get("https://www.nseindia.com/api/corporates-corporateActions",
              params={"index": "equities", "from_date": f"{start:%d-%m-%Y}", "to_date": f"{end:%d-%m-%Y}"},
              headers=h, timeout=60)
    r.raise_for_status()
    rows = []
    for x in r.json():
        subj = x.get("subject", "")
        f = _factor(subj)
        kind = "split_bonus" if f else ("demerger" if "demerger" in subj.lower() else None)
        if kind:
            rows.append({"symbol": x["symbol"].strip(), "isin": (x.get("isin") or "").strip(),
                         "ex_date": pd.to_datetime(x["exDate"], format="%d-%b-%Y"),
                         "factor": f if f else float("nan"), "subject": subj.strip(), "kind": kind})
    df = pd.DataFrame(rows, columns=["symbol", "isin", "ex_date", "factor", "subject", "kind"])
    # one bonus + one split on the same ex-date multiply; exact duplicates are dropped
    df = df.drop_duplicates(["symbol", "ex_date", "subject"])
    return df
