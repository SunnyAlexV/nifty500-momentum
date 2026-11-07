"""Parse raw bhavcopy zips into DuckDB table `bhav` (idempotent, resumable).

usage: python scripts/ingest_bhav.py [max_seconds]
"""
import sys, time
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from n500.bhavcopy import parse
from n500.db import connect, ROOT

def main(max_s=1e9):
    t0 = time.time(); con = connect()
    done = {r[0].strftime("%Y%m%d") for r in con.execute("SELECT DISTINCT date FROM bhav").fetchall()}
    files = sorted(p for p in (ROOT / "data/raw/bhav").rglob("*.csv.zip") if p.name[:8] not in done)
    print(len(files), "files to ingest", flush=True)
    buf = []
    for i, f in enumerate(files):
        buf.append(parse(f))
        if len(buf) == 100 or i == len(files) - 1:
            df = pd.concat(buf, ignore_index=True)
            # duplicate (symbol, series) rows inside one file -> keep the higher-volume one
            df = df.sort_values("volume", na_position="first").drop_duplicates(["date", "symbol", "series"], keep="last")
            con.execute("INSERT INTO bhav SELECT * FROM df"); buf = []
            if time.time() - t0 > max_s:
                print("time budget reached at", f.name, flush=True); return
    print("complete", con.execute("SELECT count(*), count(DISTINCT date), min(date), max(date) FROM bhav").fetchone())

if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 1e9)
