"""Data-layer tests: parser normalisation and stored-table integrity."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from n500.bhavcopy import parse, COLUMNS
from n500.db import connect, ROOT

RAW = ROOT / "data/raw/bhav"
pytestmark = pytest.mark.skipif(not (RAW / "2015").exists(),
                                reason="full raw history not present (fresh clone)")

@pytest.mark.parametrize("f", ["2015/20150102", "2020/20200713", "2024/20240705", "2024/20240708"])
def test_parse_schema_both_formats(f):
    df = parse(RAW / f"{f}.csv.zip")
    assert list(df.columns) == COLUMNS
    assert df["date"].nunique() == 1 and df["date"].iloc[0].strftime("%Y%m%d") == f[-8:]
    assert set(df["series"]) <= {"EQ", "BE", "BZ", "RR"}
    assert (df["close"] > 0).all()

def test_format_boundary_is_continuous():
    a, b = parse(RAW / "2024/20240705.csv.zip"), parse(RAW / "2024/20240708.csv.zip")
    m = a.merge(b, on=["isin", "series"], suffixes=("_a", "_b"))
    assert len(m) > 1500
    assert (abs(m["prev_close_b"] / m["close_a"] - 1) < 1e-6).mean() > 0.99

@pytest.fixture(scope="module")
def con():
    return connect(read_only=True)

def test_unique_keys(con):
    assert con.execute("select count(*) from (select date,symbol,series from bhav group by all having count(*)>1)").fetchone()[0] == 0

def test_coverage(con):
    n, d0, d1 = con.execute("select count(distinct date), min(date), max(date) from bhav").fetchone()
    assert n > 250

def test_price_identities(con):
    assert con.execute("select count(*) from bhav where high<low or close>high*1.0001 or close<low*0.9999 or close<=0").fetchone()[0] == 0

def test_prev_close_links_consecutive_eq_days(con):
    """From Jul-2011 (ISIN present) prev_close must equal the prior close on adjacent EQ days,
    except on corporate-action ex-dates -> breaks must be rare."""
    frac = con.execute("""
      with x as (select date, isin, close, prev_close,
                 lag(close) over w pc, lag(date) over w pd
                 from bhav where series='EQ' and date>='2011-07-01' window w as (partition by isin order by date)),
      cal as (select date, lag(date) over (order by date) prev_day from (select distinct date from bhav))
      select avg((abs(prev_close/pc-1)<1e-6)::int) from x join cal using(date) where pd = cal.prev_day""").fetchone()[0]
    assert frac > 0.998
