"""DuckDB store. One file: data/n500.duckdb.

Tables
  bhav(date, symbol, series, isin, open, high, low, close, last, prev_close,
       volume, turnover, trades)   -- raw, unadjusted, one row per (date, symbol, series)
"""
import os
from pathlib import Path
import duckdb

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("N500_DB", ROOT / "data" / "n500.duckdb"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS bhav (
  date DATE, symbol VARCHAR, series VARCHAR, isin VARCHAR,
  open DOUBLE, high DOUBLE, low DOUBLE, close DOUBLE, last DOUBLE, prev_close DOUBLE,
  volume BIGINT, turnover DOUBLE, trades BIGINT
);  -- uniqueness of (date, symbol, series) enforced at ingest and checked in tests
"""

DAYS = ROOT / "data" / "days"   # one parquet per trading day (last ~300 days), committed to git


def _sync_from_days(c) -> None:
    """Insert any per-day parquet files newer than what the DB holds (fresh clone / git pull)."""
    files = sorted(DAYS.glob("*.parquet"))
    if not files:
        return
    last = c.execute("SELECT max(date) FROM bhav").fetchone()[0]
    new = [f.as_posix() for f in files if last is None or f.stem > last.strftime("%Y%m%d")]
    if new:
        c.execute("INSERT INTO bhav SELECT * FROM read_parquet(?)", [new])


def connect(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open the price DB, creating/topping it up from data/days/ when needed."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not read_only or not DB_PATH.exists():
        with duckdb.connect(str(DB_PATH)) as c:
            c.execute(SCHEMA)
            _sync_from_days(c)
    return duckdb.connect(str(DB_PATH), read_only=read_only)
