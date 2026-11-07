"""Volatility-adjusted cross-sectional momentum Z-scores for the current Nifty 500.

Notation (t = latest close, all returns are log returns of split/bonus-adjusted prices)
  l_s        = ln(close_s / adj_prev_close_s),  adj_prev_close_s = prev_close_s / f_s
               f_s = share-count factor of a bonus/split with ex-date s (1 otherwise)
  R_6M       = sum of l over the last 126 trading days
  R_12M      = sum of l over the last 252 trading days
  R_6-1M     = R_6M  minus the last 21 days   (skip-month)
  R_12-1M    = R_12M minus the last 21 days
  sigma      = stdev(l over last 252 days) * sqrt(252)
  S_i        = R_i / sigma_i                 (or the composite below)
  Z_i        = (S_i - mean_S) / sd_S          across the eligible universe
  composite  = mean of the two per-horizon Z-scores, re-standardised
"""
from __future__ import annotations

import numpy as np
import pandas as pd

D6, D12, SKIP, VOL = 126, 252, 21, 252
SERIES_PREF = {"EQ": 0, "RR": 0, "BE": 1, "BZ": 2}

VARIANTS = {
    "Composite 6M + 12M": ("comp", "R6", "R12"),
    "Composite 6-1M + 12-1M": ("comp", "R6_1", "R12_1"),
    "6M / σ (original)": ("single", "R6"),
    "6-1M / σ": ("single", "R6_1"),
    "12-1M / σ": ("single", "R12_1"),
}


class _UF:
    def __init__(self): self.p = {}
    def find(self, x):
        self.p.setdefault(x, x)
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]; x = self.p[x]
        return x
    def union(self, a, b): self.p[self.find(a)] = self.find(b)


def load_window(con, universe: pd.DataFrame, n_days: int = D12 + 10) -> tuple[pd.DataFrame, pd.DatetimeIndex]:
    """Rows for universe stocks over the last n_days market days, one row per (stock, date).

    A stock's ISIN changes at a face-value split and its symbol changes on a rename, so rows are
    linked into one history with union-find over (isin, symbol) pairs seen in the window."""
    cal = pd.DatetimeIndex(con.execute(
        f"SELECT DISTINCT date FROM bhav ORDER BY date DESC LIMIT {n_days}").df()["date"]).sort_values()
    start = cal[0]
    raw = con.execute("SELECT * FROM bhav WHERE date >= ?", [start.date()]).df()
    uf = _UF()
    for isin, sym in raw[["isin", "symbol"]].drop_duplicates().itertuples(index=False):
        uf.union(("I", isin), ("S", sym))
    root_to_key = {}
    for sym, isin in universe[["symbol", "isin"]].itertuples(index=False):
        for node in (("S", sym), ("I", isin)):
            if node in uf.p:
                root_to_key.setdefault(uf.find(node), sym)
    raw["key"] = [root_to_key.get(uf.find(("I", i))) for i in raw["isin"]]
    raw = raw.dropna(subset=["key"])
    raw["pref"] = raw["series"].map(SERIES_PREF)
    raw = raw.sort_values(["key", "date", "pref"]).drop_duplicates(["key", "date"])
    return raw, cal


def adjusted_log_returns(raw: pd.DataFrame, actions: pd.DataFrame, cal: pd.DatetimeIndex):
    """Log-return panel (dates x stocks) plus audit tables.

    Rules, in order:
      1. split/bonus on ex-date s:  l_s = ln(close_s * f_s / prev_close_s)
      2. demerger ex-date with |l_s| > ln(1.35): l_s := missing. NSE normally re-bases the
         reference price; when it does not, the drop is value moved into the new company's
         shares, not a loss to the holder.
      3. listing day of a stock that entered inside the window: l := missing (the reference
         is the IPO / discovery price, not a traded close).
      4. any remaining |l| > ln(1.35) is kept but reported for manual checking."""
    raw = raw.copy()
    sym2key = dict(zip(raw["symbol"], raw["key"])); isin2key = dict(zip(raw["isin"], raw["key"]))
    a = actions.copy()
    a["key"] = [sym2key.get(s) or isin2key.get(i) for s, i in zip(a["symbol"], a["isin"])]
    a = a.dropna(subset=["key"])
    sb = a[a["kind"] == "split_bonus"].groupby(["key", "ex_date"], as_index=False).agg(
        factor=("factor", "prod"), subject=("subject", " + ".join))
    dm = a[a["kind"] == "demerger"][["key", "ex_date"]].drop_duplicates().assign(demerger=True)
    raw = raw.merge(sb.rename(columns={"ex_date": "date"}), on=["key", "date"], how="left")
    raw = raw.merge(dm.rename(columns={"ex_date": "date"}), on=["key", "date"], how="left")
    raw["factor"] = raw["factor"].fillna(1.0)
    raw["demerger"] = raw["demerger"].astype("boolean").fillna(False).astype(bool)
    raw["l"] = np.log(raw["close"] / (raw["prev_close"] / raw["factor"]))
    big = raw["l"].abs() > np.log(1.35)
    first = raw.groupby("key")["date"].transform("min")
    raw["note"] = ""
    raw.loc[raw["factor"] != 1, "note"] = "split/bonus adjusted: " + raw["subject"].fillna("")
    m = raw["demerger"] & big
    raw.loc[m, "note"] = "demerger ex-date jump set to missing"; raw.loc[m, "l"] = np.nan
    m = (raw["date"] == first) & (first > cal[0])
    raw.loc[m, "note"] = "listing day dropped"; raw.loc[m, "l"] = np.nan
    m = big & (raw["note"] == "")
    raw.loc[m, "note"] = "UNEXPLAINED move > 35% (kept) - check"
    audit = raw.loc[raw["note"] != "", ["key", "date", "close", "prev_close", "factor", "l", "note"]]
    L = raw.pivot(index="date", columns="key", values="l")
    T = raw.pivot(index="date", columns="key", values="turnover")
    C = raw.pivot(index="date", columns="key", values="close")
    return L, T, C, audit.sort_values("date")


def zscore(x: pd.Series) -> pd.Series:
    return (x - x.mean()) / x.std(ddof=1)


def compute(L: pd.DataFrame, T: pd.DataFrame, C: pd.DataFrame, cal: pd.DatetimeIndex,
            min_mdtv_cr: float = 5.0, min_obs: float = 0.9) -> tuple[pd.DataFrame, pd.DataFrame]:
    L = L.reindex(cal); T = T.reindex(cal).fillna(0.0); C = C.reindex(cal).ffill()
    Lw = L.iloc[-VOL:]
    f = pd.DataFrame(index=L.columns)
    f["R6"] = L.iloc[-D6:].sum(min_count=1)
    f["R12"] = L.iloc[-D12:].sum(min_count=1)
    f["R6_1"] = L.iloc[-D6:-SKIP].sum(min_count=1)
    f["R12_1"] = L.iloc[-D12:-SKIP].sum(min_count=1)
    f["sigma"] = Lw.std(ddof=1) * np.sqrt(252)
    f["obs"] = Lw.notna().sum()
    f["mdtv_cr"] = T.iloc[-D6:].median() / 1e7
    f["last_close"] = C.iloc[-1]
    f["last_trade"] = L.apply(lambda s: s.last_valid_index())

    why = pd.Series("", index=f.index)
    why[f["obs"] < min_obs * VOL] = "under 1 year of prices"
    why[(why == "") & (f["last_trade"] < cal[-5])] = "not traded in last 5 days"
    why[(why == "") & (f["mdtv_cr"] < min_mdtv_cr)] = f"MDTV below ₹{min_mdtv_cr:g} cr"
    f["excluded"] = why
    e = f[why == ""].copy()

    for name, spec in VARIANTS.items():
        if spec[0] == "single":
            s = e[spec[1]] / e["sigma"]
        else:
            s = (zscore(e[spec[1]] / e["sigma"]) + zscore(e[spec[2]] / e["sigma"])) / 2
        e[name] = zscore(s)
    return e, f[why != ""]
