"""Nifty 500 volatility-adjusted momentum Z-scores.

Run:  streamlit run app.py
Each refresh: fetches any new NSE bhavcopy, the current Nifty 500 list and recent
split/bonus/demerger actions, then recomputes every score from the latest close.
"""
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from n500.db import connect, ROOT
from n500.update import update_prices
from n500.nse import nifty500_constituents, split_bonus_actions
from n500.signals import load_window, adjusted_log_returns, compute, VARIANTS

CACHE = ROOT / "data" / "cache"; CACHE.mkdir(parents=True, exist_ok=True)
st.set_page_config(page_title="Nifty 500 Momentum", layout="wide")


def with_fallback(fetch, name):
    """Use the live source; if NSE/niftyindices is unreachable, fall back to the last good copy."""
    path = CACHE / f"{name}.parquet"
    try:
        df = fetch(); df.to_parquet(path); return df, None
    except Exception as e:  # noqa: BLE001
        if path.exists():
            return pd.read_parquet(path), f"{name}: live fetch failed ({type(e).__name__}); using saved copy"
        raise


@st.cache_data(ttl=900, show_spinner="Updating prices and recomputing scores…")
def run(min_mdtv_cr: float):
    warns = []
    con = connect()
    try:
        new_days = update_prices(con)
    except Exception as e:  # noqa: BLE001
        new_days = []; warns.append(f"price update failed ({type(e).__name__}); using stored prices")
    con.close()
    con = connect(read_only=True)
    uni, w = with_fallback(nifty500_constituents, "nifty500"); warns += [w] if w else []
    raw, cal = load_window(con, uni)
    acts, w = with_fallback(lambda: split_bonus_actions(cal[0].date() - timedelta(days=7),
                                                        date.today() + timedelta(days=1)), "actions")
    warns += [w] if w else []
    L, T, C, audit = adjusted_log_returns(raw, acts, cal)
    elig, excl = compute(L, T, C, cal, min_mdtv_cr=min_mdtv_cr)
    con.close()
    meta = uni.set_index("symbol")[["company", "industry"]]
    unmatched = sorted(set(uni["symbol"]) - set(raw["key"]))
    return elig.join(meta), excl.join(meta), audit, cal[-1], new_days, unmatched, warns


# ---------------------------------------------------------------- sidebar
st.sidebar.header("Settings")
variant = st.sidebar.selectbox("Signal", list(VARIANTS), index=0)
mdtv = st.sidebar.number_input("Min. 6-month median daily traded value (₹ crore)", 0.0, 100.0, 5.0, 0.5)
if st.sidebar.button("Refresh now", type="primary"):
    run.clear()
st.sidebar.caption("Results are cached for 15 minutes; **Refresh now** forces a fresh pull. "
                   "NSE publishes the day's bhavcopy in the evening (IST), so during market hours "
                   "scores are as of the previous close.")

elig, excl, audit, asof, new_days, unmatched, warns = run(mdtv)
for w in warns:
    st.warning(w)

# ---------------------------------------------------------------- header
st.title("Nifty 500 · Volatility-Adjusted Momentum")
n = len(elig); top10 = -(-n // 10); top20 = -(-n // 5)
df = elig.sort_values(variant, ascending=False).copy()
df.insert(0, "Rank", range(1, n + 1))
c1, c2, c3, c4 = st.columns(4)
c1.metric("Prices as of", asof.strftime("%d %b %Y"))
c2.metric("Stocks scored", f"{n}  ({len(excl)} excluded)")
c3.metric(f"Top 10% (rank ≤ {top10}) cut-off Z", f"{df[variant].iloc[top10 - 1]:.2f}")
c4.metric("Rank 60 Z", f"{df[variant].iloc[59]:.2f}" if n >= 60 else "–")
if new_days:
    st.caption(f"Added {len(new_days)} new trading day(s): " + ", ".join(d.strftime('%d %b') for d in new_days))

# ---------------------------------------------------------------- main table
def band(r):
    return "Top 10%" if r <= top10 else ("Top 20%" if r <= top20 else ("≤ 60" if r <= 60 else ""))

df["Band"] = df["Rank"].map(band)
show = pd.DataFrame({
    "Rank": df["Rank"], "Symbol": df.index, "Company": df["company"], "Industry": df["industry"],
    "Z": df[variant], "Band": df["Band"],
})
import numpy as np  # noqa: E402
show["6M ret %"] = 100 * (np.exp(df["R6"]) - 1)
show["12M ret %"] = 100 * (np.exp(df["R12"]) - 1)
show["6-1M ret %"] = 100 * (np.exp(df["R6_1"]) - 1)
show["12-1M ret %"] = 100 * (np.exp(df["R12_1"]) - 1)
show["σ ann. %"] = 100 * df["sigma"]
show["MDTV ₹cr"] = df["mdtv_cr"]
show["Last close"] = df["last_close"]

tab1, tabP, tab2, tab3, tab4 = st.tabs(["Ranking", "My portfolio", "All signals", "Excluded", "Data checks"])

with tabP:
    from n500.portfolio import build, parse_holdings
    st.caption(f"Uses the **{variant}** ranking above. Keeps holdings ranked ≤ the exit rank, sells the rest, "
               "fills empty slots from the top while respecting the industry cap, then sizes in whole shares.")
    a1, a2, a3 = st.columns(3)
    value = a1.number_input("Total portfolio value (holdings + cash), ₹", 10_000, 100_000_000, 250_000, 5_000)
    n_hold = a2.number_input("Number of stocks (N)", 5, 60, 20, 1)
    exit_rank = a3.number_input("Sell when rank falls below", 10, 200, 60, 5)
    b1, b2, b3 = st.columns(3)
    weighting = b1.radio("Weighting", ["Inverse volatility", "Equal"], horizontal=True)
    ind_cap = b2.slider("Industry cap (% of names)", 10, 100, 35, 5)
    max_w = b3.slider("Max weight per stock (%)", 5, 30, 10, 1)
    rebal = st.checkbox("Also top up / trim kept holdings to target weights", value=False,
                        help="Off: kept stocks are left alone and only new buys are sized (fewer trades).")
    htxt = st.text_area("Current holdings: one per line, SYMBOL QUANTITY (leave empty for a fresh start)",
                        placeholder="LAURUSLABS 7\nCUPID 27", height=140)
    holdings = parse_holdings(htxt)
    out = build(elig.rename(columns={"industry": "industry"}), variant, holdings, value=float(value),
                n=int(n_hold), exit_rank=int(exit_rank), industry_cap=ind_cap / 100,
                max_weight=max_w / 100, weighting="equal" if weighting == "Equal" else "inverse_vol",
                rebalance_kept=rebal, prices=excl["last_close"])
    p, sells, cash = out["portfolio"], out["sells"], out["cash"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Sells", len(sells)); m2.metric("Buys", int((p["trade_qty"] > 0).sum()))
    m3.metric("Cost of buys", f"₹{cash['buy_cost']:,.0f}")
    m4.metric("Cash left after trades", f"₹{cash['cash_after']:,.0f}")
    if cash["cash_after"] < 0:
        st.error("Buys cost more than the cash available. Check the portfolio value, or tick top up / trim.")
    if cash["unpriced"]:
        st.warning("No current price for: " + ", ".join(cash["unpriced"]) + " (not in the eligible list); "
                   "their value is not counted.")

    st.subheader("Orders")
    sell_orders = pd.DataFrame({"Action": "SELL", "Symbol": sells["symbol"], "Qty": -sells["trade_qty"],
                                "Last close": sells["last_close"], "Value ₹": -sells["trade_value"],
                                "Why": sells["reason"]})
    t = p[p["trade_qty"] != 0]
    buy_orders = pd.DataFrame({"Action": np.where(t["trade_qty"] > 0, "BUY", "SELL"), "Symbol": t.index,
                               "Qty": t["trade_qty"].abs().values, "Last close": t["last_close"].values,
                               "Value ₹": t["trade_value"].abs().values,
                               "Why": np.where(t["status"] == "BUY", "new: rank " + t["rank"].astype(str),
                                               "rebalance kept holding")})
    orders = pd.concat([sell_orders, buy_orders], ignore_index=True)
    if orders.empty:
        st.info("No trades needed this month.")
    else:
        st.dataframe(orders, hide_index=True, use_container_width=True,
                     column_config={"Last close": st.column_config.NumberColumn(format="₹%.2f"),
                                    "Value ₹": st.column_config.NumberColumn(format="₹%.0f")})
        st.download_button("Download orders CSV", orders.to_csv(index=False).encode(),
                           f"orders_{asof:%Y%m%d}.csv", "text/csv")

    st.subheader("Portfolio after trades")
    port = pd.DataFrame({"Rank": p["rank"], "Symbol": p.index, "Status": p["status"], "Industry": p["industry"],
                         "Z": p[variant], "σ ann. %": 100 * p["sigma"], "Target weight %": 100 * p["weight"],
                         "Qty": p["new_qty"], "Value ₹": p["new_qty"] * p["last_close"]})
    st.dataframe(port, hide_index=True, use_container_width=True,
                 column_config={"Z": st.column_config.NumberColumn(format="%.2f"),
                                "σ ann. %": st.column_config.NumberColumn(format="%.1f"),
                                "Target weight %": st.column_config.NumberColumn(format="%.1f"),
                                "Value ₹": st.column_config.NumberColumn(format="₹%.0f")})
    ind = port.groupby("Industry").agg(names=("Symbol", "count"), value=("Value ₹", "sum"))
    ind["% of invested"] = 100 * ind["value"] / ind["value"].sum()
    st.caption(f"Industry exposure (cap: {out['max_per_industry']} names per industry)")
    st.dataframe(ind.sort_values("value", ascending=False).round(1), use_container_width=True)
    if len(out["skipped"]):
        with st.expander(f"Skipped while filling ({len(out['skipped'])})"):
            st.dataframe(out["skipped"], hide_index=True, use_container_width=True)
with tab1:
    q = st.text_input("Filter symbol / company / industry", "")
    view = show
    if q:
        m = show[["Symbol", "Company", "Industry"]].apply(lambda s: s.str.contains(q, case=False, na=False)).any(axis=1)
        view = show[m]
    st.dataframe(
        view, hide_index=True, use_container_width=True, height=640,
        column_config={
            "Z": st.column_config.NumberColumn(format="%.2f"),
            **{k: st.column_config.NumberColumn(format="%.1f") for k in
               ["6M ret %", "12M ret %", "6-1M ret %", "12-1M ret %", "σ ann. %", "MDTV ₹cr"]},
            "Last close": st.column_config.NumberColumn(format="₹%.2f"),
        })
    st.download_button("Download CSV", show.to_csv(index=False).encode(),
                       f"nifty500_z_{asof:%Y%m%d}_{variant.split()[0]}.csv", "text/csv")

with tab2:
    allz = elig[list(VARIANTS)].copy()
    for v in VARIANTS:
        allz[f"rank · {v}"] = allz[v].rank(ascending=False).astype(int)
    allz = allz.sort_values(variant, ascending=False)
    allz.insert(0, "Company", elig["company"])
    st.dataframe(allz, use_container_width=True, height=600,
                 column_config={v: st.column_config.NumberColumn(format="%.2f") for v in VARIANTS})
    st.caption("Spearman rank correlation between signals")
    st.dataframe(elig[list(VARIANTS)].corr(method="spearman").round(2), use_container_width=True)

with tab3:
    st.dataframe(excl[["company", "excluded", "obs", "mdtv_cr"]].rename(
        columns={"obs": "days of prices (of 252)", "mdtv_cr": "MDTV ₹cr"}), use_container_width=True)
    if unmatched:
        st.caption("In the Nifty 500 list but no NSE prices found (placeholder tickers): " + ", ".join(unmatched))

with tab4:
    st.caption("Every price-history adjustment applied in the 1-year window. "
               "Rows marked UNEXPLAINED are real data kept as-is; worth a glance.")
    st.dataframe(audit.sort_values("date", ascending=False), hide_index=True, use_container_width=True)

with st.expander("Method"):
    st.latex(r"\ell_{i,s}=\ln\frac{P_{i,s}\,f_{i,s}}{P^{\text{prev}}_{i,s}},\qquad "
             r"R^{(h)}_i=\sum_{s\in h}\ell_{i,s},\qquad "
             r"\sigma_i=\sqrt{252}\;\mathrm{sd}\big(\ell_{i,t-251..t}\big)")
    st.latex(r"S_i=\frac{R_i}{\sigma_i},\qquad Z_i=\frac{S_i-\mu_S}{\sigma_S}\quad"
             r"\text{(composite: }Z_i=\operatorname{std}\big[\tfrac12(z^{(a)}_i+z^{(b)}_i)\big]\text{)}")
    st.markdown("6M = 126 days, 12M = 252 days, skip-month variants drop the last 21 days. "
                "*f* is the share-count factor of a split/bonus on its ex-date. Eligible: ≥ 90% of the "
                "last 252 days priced, traded in the last 5 days, MDTV above the threshold. "
                "Price returns only (dividends not included).")
