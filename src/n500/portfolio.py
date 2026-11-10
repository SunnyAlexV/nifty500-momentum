"""Turn a Z-score ranking into a concrete portfolio and a monthly trade list.

Rules (applied in this order)
  1. Keep  : a stock you hold stays while its rank <= exit_rank (default 60).
  2. Sell  : a holding ranked below exit_rank, or no longer in the eligible universe.
  3. Buy   : fill the empty slots up to N from the top of the ranking, skipping stocks
             already held, industries already at the cap, and stocks whose single share
             costs more than their allocation.
  4. Weight: inverse-volatility  w_i = (1/sigma_i) / sum_j (1/sigma_j)   (or equal weight),
             then capped at max_weight with the excess redistributed pro rata.
  5. Size  : target shares = floor(portfolio_value * w_i / price_i).
  6. Fill  : spend cash left by rounding one share at a time on the name furthest below
             its target value (relative shortfall), never above max_weight.
"""
from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd


def parse_holdings(text: str) -> pd.Series:
    """'LAURUSLABS 7' / 'cupid, 27' / 'DIVISLAB' per line -> Series symbol -> qty (0 if not given)."""
    out = {}
    for line in (text or "").splitlines():
        parts = [p for p in re.split(r"[\s,;:\t]+", line.strip()) if p]
        if not parts:
            continue
        sym = parts[0].upper()
        qty = int(float(parts[1])) if len(parts) > 1 and re.fullmatch(r"\d+(\.\d+)?", parts[1]) else 0
        out[sym] = out.get(sym, 0) + qty
    return pd.Series(out, dtype="int64")


def capped_weights(raw: pd.Series, max_weight: float) -> pd.Series:
    """Normalise raw scores to weights summing to 1 with no weight above max_weight.

    Water-filling: fix any weight over the cap at the cap and share the remaining budget
    among the others in proportion to their raw scores; repeat until nothing exceeds it."""
    raw = raw.astype(float)
    if raw.empty:
        return raw
    cap = max(max_weight, 1.0 / len(raw))          # a cap below 1/N is infeasible
    w = pd.Series(0.0, index=raw.index)
    free = pd.Series(True, index=raw.index)
    budget = 1.0
    for _ in range(len(raw)):
        w[free] = budget * raw[free] / raw[free].sum()
        over = free & (w > cap + 1e-12)
        if not over.any():
            break
        w[over] = cap
        free &= ~over
        budget = 1.0 - w[~free].sum()
    return w


def build(ranked: pd.DataFrame, score_col: str, holdings: pd.Series, *, value: float,
          n: int = 20, exit_rank: int = 60, industry_cap: float = 0.35,
          max_weight: float = 0.10, weighting: str = "inverse_vol",
          rebalance_kept: bool = False, fill_cash: bool = True,
          prices: pd.Series | None = None) -> dict:
    """ranked: eligible universe with columns [score_col, sigma, last_close, industry].
    prices: optional last closes for other symbols (e.g. excluded stocks) to value holdings.

    Returns dict(portfolio, trades, skipped, cash) — all DataFrames except cash (dict)."""
    r = ranked.sort_values(score_col, ascending=False).copy()
    r["rank"] = np.arange(1, len(r) + 1)
    max_per_ind = max(1, math.floor(industry_cap * n + 1e-9))

    held = holdings.index.tolist()
    keep = [s for s in held if s in r.index and r.at[s, "rank"] <= exit_rank]
    sell = [s for s in held if s not in keep]
    skipped = {}

    def weights(names):
        if weighting == "equal":
            raw = pd.Series(1.0, index=names)
        else:
            raw = 1.0 / r.loc[names, "sigma"]
        return capped_weights(raw, max_weight)

    banned: set[str] = set()
    while True:
        chosen = list(keep)
        ind_count = r.loc[chosen, "industry"].value_counts().to_dict() if chosen else {}
        for s in r.index:
            if len(chosen) >= n:
                break
            if s in chosen or s in held or s in banned:
                continue
            ind = r.at[s, "industry"]
            if ind_count.get(ind, 0) >= max_per_ind:
                skipped.setdefault(s, f"industry cap ({ind}: {max_per_ind} names)")
                continue
            chosen.append(s)
            ind_count[ind] = ind_count.get(ind, 0) + 1
        w = weights(chosen)
        target_val = value * w
        too_dear = [s for s in chosen if s not in keep and r.at[s, "last_close"] > target_val[s]]
        if not too_dear:
            break
        for s in too_dear:
            banned.add(s)
            skipped[s] = (f"one share (₹{r.at[s, 'last_close']:,.0f}) costs more than its "
                          f"allocation (₹{target_val[s]:,.0f})")

    p = r.loc[chosen, ["rank", score_col, "industry", "sigma", "last_close"]].copy()
    p["weight"] = w
    p["target_value"] = value * w
    p["target_qty"] = np.floor(p["target_value"] / p["last_close"]).astype(int)
    p["held_qty"] = [int(holdings.get(s, 0)) for s in p.index]
    p["status"] = ["KEEP" if s in keep else "BUY" for s in p.index]
    if rebalance_kept:
        p["trade_qty"] = p["target_qty"] - p["held_qty"]
    else:
        p["trade_qty"] = np.where(p["status"] == "BUY", p["target_qty"], 0)
    p["new_qty"] = p["held_qty"] + p["trade_qty"]
    p["trade_value"] = p["trade_qty"] * p["last_close"]
    p = p.sort_values("rank")

    px_all = r["last_close"].copy()
    if prices is not None:
        px_all = px_all.combine_first(prices)
    held_val = sum(int(holdings[s]) * px_all[s] for s in held if s in px_all.index)

    # Leftover pass: rounding down leaves cash idle. Spend it one share at a time on the
    # tradable name furthest below its target (relative shortfall), never breaching max_weight.
    if fill_cash:
        tradable = p.index if rebalance_kept else p.index[p["status"] == "BUY"]
        sell_cash = sum(int(holdings[s]) * px_all.get(s, 0.0) for s in sell)
        left = (value - held_val) + sell_cash - p["trade_value"].sum()
        cap_val = max(max_weight, 1.0 / len(p)) * value
        for _ in range(10_000):
            cur = p.loc[tradable, "new_qty"] * p.loc[tradable, "last_close"]
            px = p.loc[tradable, "last_close"]
            ok = (px <= left + 1e-9) & (cur + px <= cap_val + 1e-9)
            if not ok.any():
                break
            short = ((p.loc[tradable, "target_value"] - cur) / p.loc[tradable, "target_value"])[ok]
            s = short.idxmax()
            p.at[s, "new_qty"] += 1; p.at[s, "trade_qty"] += 1
            left -= p.at[s, "last_close"]
        p["trade_value"] = p["trade_qty"] * p["last_close"]

    # sells: price from the ranking or the supplied price list, else unknown
    sell_rows = []
    for s in sell:
        px = px_all[s] if s in px_all.index else np.nan
        why = (f"rank {int(r.at[s, 'rank'])} > {exit_rank}" if s in r.index
               else "not in the eligible Nifty 500 universe")
        sell_rows.append(dict(symbol=s, held_qty=int(holdings[s]), last_close=px,
                              trade_qty=-int(holdings[s]), trade_value=-int(holdings[s]) * px, reason=why))
    sells = pd.DataFrame(sell_rows, columns=["symbol", "held_qty", "last_close", "trade_qty", "trade_value", "reason"])

    unknown = [s for s in held if s not in px_all.index and holdings[s] > 0]
    cash_before = value - held_val
    proceeds = -sells["trade_value"].sum(skipna=True)
    spend = p["trade_value"].clip(lower=0).sum()
    trims = -p["trade_value"].clip(upper=0).sum()
    cash = dict(value=value, holdings_value=held_val, cash_before=cash_before,
                sell_proceeds=proceeds + trims, buy_cost=spend,
                cash_after=cash_before + proceeds + trims - spend, unpriced=unknown)
    sk = pd.DataFrame([(s, int(r.at[s, "rank"]), why) for s, why in skipped.items() if s not in p.index],
                      columns=["symbol", "rank", "reason"]).sort_values("rank")
    return dict(portfolio=p, sells=sells, skipped=sk, cash=cash, max_per_industry=max_per_ind)
