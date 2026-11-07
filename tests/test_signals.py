"""Signal maths on synthetic data, where the right answer is known exactly."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from n500.nse import _factor
from n500.signals import adjusted_log_returns, compute, zscore, VARIANTS, D6, D12, SKIP


@pytest.mark.parametrize("subject,f", [
    ("Bonus 1:1", 2.0), ("Bonus 3:1", 4.0), ("Bonus 1:2", 1.5),
    ("Face Value Split (Sub-Division) - From Rs 10/- Per Share To Re 1/- Per Share", 10.0),
    ("Face Value Split (Sub-Division) - From Rs 2/- Per Share To Re 0.50/- Per Share", 4.0),
    ("Consolidation Of Shares From Re 1/- Per Share To Rs 10/- Per Share", 0.1),
    ("Scheme Of Arrangement - Bonus Ncrps 4:1", None), ("Dividend - Rs 10 Per Share", None)])
def test_factor(subject, f):
    assert _factor(subject) == f


def _panel(n=300, k=40, seed=0):
    """k stocks, n days, constant daily log drift mu_i plus noise; returns raw rows + calendar."""
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range("2025-01-01", periods=n)
    rows = []
    for i in range(k):
        l = 0.0005 * (i - k / 2) + 0.01 * rng.standard_normal(n)
        p = 100 * np.exp(np.cumsum(l))
        prev = np.r_[100.0, p[:-1]]
        for d, c, pc in zip(cal, p, prev):
            rows.append(dict(key=f"S{i}", symbol=f"S{i}", isin=f"I{i}", date=d, close=c,
                             prev_close=pc, turnover=1e9, series="EQ"))
    return pd.DataFrame(rows), cal


NO_ACTIONS = pd.DataFrame(columns=["symbol", "isin", "ex_date", "factor", "subject", "kind"])


def test_returns_match_definition():
    raw, cal = _panel()
    L, T, C, _ = adjusted_log_returns(raw, NO_ACTIONS, cal)
    e, _ = compute(L, T, C, cal)
    p = raw[raw.key == "S3"].set_index("date")["close"]
    assert e.loc["S3", "R6"] == pytest.approx(np.log(p.iloc[-1] / p.iloc[-1 - D6]))
    assert e.loc["S3", "R12"] == pytest.approx(np.log(p.iloc[-1] / p.iloc[-1 - D12]))
    assert e.loc["S3", "R6_1"] == pytest.approx(np.log(p.iloc[-1 - SKIP] / p.iloc[-1 - D6]))
    l = np.log(p / p.shift()).iloc[-252:]
    assert e.loc["S3", "sigma"] == pytest.approx(l.std(ddof=1) * np.sqrt(252))


def test_split_is_neutral():
    """A 1:5 split (price / 5 overnight) must leave every score unchanged."""
    raw, cal = _panel()
    L0, T0, C0, _ = adjusted_log_returns(raw, NO_ACTIONS, cal)
    base, _ = compute(L0, T0, C0, cal)
    ex = cal[200]
    m = (raw.key == "S7") & (raw.date >= ex)
    raw2 = raw.copy()
    raw2.loc[m, "close"] /= 5
    raw2.loc[m & (raw2.date > ex), "prev_close"] /= 5   # ex-date prev_close is the pre-split close
    acts = pd.DataFrame([dict(symbol="S7", isin="I7", ex_date=ex, factor=5.0, subject="split", kind="split_bonus")])
    L, T, C, audit = adjusted_log_returns(raw2, acts, cal)
    out, _ = compute(L, T, C, cal)
    assert np.allclose(out.loc["S7", ["R6", "R12", "sigma"]].astype(float),
                       base.loc["S7", ["R6", "R12", "sigma"]].astype(float))
    assert audit["note"].str.startswith("split").sum() == 1


def test_zscores_standardised_and_ordered():
    raw, cal = _panel()
    L, T, C, _ = adjusted_log_returns(raw, NO_ACTIONS, cal)
    e, _ = compute(L, T, C, cal)
    for v in VARIANTS:
        assert e[v].mean() == pytest.approx(0, abs=1e-12) and e[v].std(ddof=1) == pytest.approx(1)
    # higher drift -> higher momentum: rank correlation with true drift must be strongly positive
    drift = pd.Series({f"S{i}": i for i in range(40)})
    assert e["6M / σ (original)"].rank().corr(drift.rank()) > 0.8


def test_composite_is_average_of_horizon_z():
    raw, cal = _panel()
    L, T, C, _ = adjusted_log_returns(raw, NO_ACTIONS, cal)
    e, _ = compute(L, T, C, cal)
    exp = zscore((zscore(e.R6 / e.sigma) + zscore(e.R12 / e.sigma)) / 2)
    assert np.allclose(e["Composite 6M + 12M"], exp)


def test_listing_day_and_liquidity_and_history_rules():
    raw, cal = _panel()
    raw = raw[~((raw.key == "S1") & (raw.date < cal[100]))]       # S1 lists mid-window
    raw.loc[raw.key == "S2", "turnover"] = 1e7                       # ₹1 cr/day: illiquid
    L, T, C, audit = adjusted_log_returns(raw, NO_ACTIONS, cal)
    e, x = compute(L, T, C, cal)
    assert "listing day dropped" in audit.loc[audit.key == "S1", "note"].values
    assert x.loc["S1", "excluded"] == "under 1 year of prices"
    assert x.loc["S2", "excluded"].startswith("MDTV below")
    assert len(e) == 38
