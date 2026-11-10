"""Portfolio construction rules on a small hand-built ranking."""
import sys
from pathlib import Path
import numpy as np
import pandas as pd
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from n500.portfolio import build, capped_weights, parse_holdings

Z = "Z"


def ranking(n=30):
    """Stock k has rank k+1 (Z descending); industries cycle A,A,A,B,C,D,...; sigma rises with k."""
    inds = ["A", "A", "A"] + [chr(ord("B") + i % 8) for i in range(n - 3)]
    return pd.DataFrame({Z: np.linspace(3, -3, n), "sigma": np.linspace(0.2, 0.6, n),
                         "last_close": 100.0, "industry": inds},
                        index=[f"S{k:02d}" for k in range(n)])


def test_parse_holdings():
    h = parse_holdings("laurusLabs 7\nCUPID, 27\n\nDIVISLAB\n")
    assert h.to_dict() == {"LAURUSLABS": 7, "CUPID": 27, "DIVISLAB": 0}


def test_capped_weights_sum_and_cap():
    w = capped_weights(pd.Series([10, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1.0]), 0.15)
    assert w.sum() == pytest.approx(1) and w.max() <= 0.15 + 1e-12
    # uncapped names keep their raw proportions
    assert w.iloc[1:].nunique() == 1


def test_inverse_vol_weights_without_cap():
    r = ranking()
    out = build(r, Z, pd.Series(dtype="int64"), value=1e6, n=5, industry_cap=1.0, max_weight=1.0)
    p = out["portfolio"]
    inv = 1 / r.loc[p.index, "sigma"]
    assert np.allclose(p["weight"], inv / inv.sum())


def test_first_month_buys_top_n():
    out = build(ranking(), Z, pd.Series(dtype="int64"), value=1e6, n=5, industry_cap=1.0)
    assert list(out["portfolio"].index) == ["S00", "S01", "S02", "S03", "S04"]
    assert (out["portfolio"]["status"] == "BUY").all()


def test_industry_cap_skips_to_next_industry():
    # n=5, cap 0.4 -> max 2 names per industry; S02 (3rd 'A') must be skipped
    out = build(ranking(), Z, pd.Series(dtype="int64"), value=1e6, n=5, industry_cap=0.4)
    assert "S02" not in out["portfolio"].index
    assert out["portfolio"]["industry"].value_counts().max() <= 2
    assert "S02" in out["skipped"]["symbol"].values


def test_exit_rank_buffer():
    # hold S10 (rank 11, inside exit 15) and S20 (rank 21, outside) -> keep one, sell one
    h = pd.Series({"S10": 5, "S20": 5})
    out = build(ranking(), Z, h, value=1e6, n=5, exit_rank=15, industry_cap=1.0)
    p = out["portfolio"]
    assert p.at["S10", "status"] == "KEEP" and p.at["S10", "trade_qty"] == 0
    assert out["sells"]["symbol"].tolist() == ["S20"]
    assert len(p) == 5 and set(p.index) == {"S10", "S00", "S01", "S02", "S03"}


def test_unlisted_holding_is_sold():
    out = build(ranking(), Z, pd.Series({"GONE": 3}), value=1e6, n=5, industry_cap=1.0)
    assert out["sells"].iloc[0]["reason"].startswith("not in the eligible")


def test_expensive_share_skipped():
    r = ranking()
    r.loc["S00", "last_close"] = 1e6          # one share > its allocation
    out = build(r, Z, pd.Series(dtype="int64"), value=1e5, n=5, industry_cap=1.0)
    assert "S00" not in out["portfolio"].index and len(out["portfolio"]) == 5
    assert out["skipped"].iloc[0]["symbol"] == "S00"


def test_whole_shares_and_budget():
    out = build(ranking(), Z, pd.Series(dtype="int64"), value=250_000, n=20, fill_cash=False)
    p = out["portfolio"]
    assert (p["target_qty"] * p["last_close"] <= p["target_value"] + 1e-9).all()
    assert out["cash"]["buy_cost"] <= 250_000 and out["cash"]["cash_after"] >= 0


def test_leftover_cash_is_spent_within_caps():
    r = ranking()
    r["last_close"] = np.linspace(900, 4000, len(r))          # coarse prices -> big rounding loss
    base = build(r, Z, pd.Series(dtype="int64"), value=250_000, n=20, fill_cash=False)["cash"]["cash_after"]
    out = build(r, Z, pd.Series(dtype="int64"), value=250_000, n=20)
    p, cash = out["portfolio"], out["cash"]
    assert cash["cash_after"] >= 0
    assert cash["cash_after"] < base                       # it spent some leftover
    assert cash["cash_after"] < p["last_close"].min()       # nothing affordable left (or caps bind)
    assert (p["new_qty"] * p["last_close"] <= 0.10 * 250_000 + 1e-6).all()


def test_holdings_valued_from_supplied_prices():
    out = build(ranking(), Z, pd.Series({"NEWLIST": 10}), value=1e5, n=5, industry_cap=1.0,
                prices=pd.Series({"NEWLIST": 50.0}))
    assert out["sells"].iloc[0]["trade_value"] == -500 and out["cash"]["holdings_value"] == 500
