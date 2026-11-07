"""Write data/days/YYYYMMDD.parquet for the last N trading days and remove older files.

usage: python scripts/make_snapshot.py [n_days=300]
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from n500.db import connect, DAYS

n = int(sys.argv[1]) if len(sys.argv) > 1 else 300
con = connect(read_only=True)
keep = [d for (d,) in con.execute(f"SELECT DISTINCT date FROM bhav ORDER BY date DESC LIMIT {n}").fetchall()]
DAYS.mkdir(parents=True, exist_ok=True)
names = {f"{d:%Y%m%d}" for d in keep}
for d in keep:
    out = DAYS / f"{d:%Y%m%d}.parquet"
    if not out.exists():
        con.execute(f"COPY (SELECT * FROM bhav WHERE date = DATE '{d}' ORDER BY symbol, series) "
                    f"TO '{out.as_posix()}' (FORMAT parquet, COMPRESSION zstd)")
for f in DAYS.glob("*.parquet"):
    if f.stem not in names:
        f.unlink()
size = sum(f.stat().st_size for f in DAYS.glob("*.parquet")) / 1e6
print(f"{len(keep)} day files, {size:.1f} MB, {min(keep)} .. {max(keep)}")
