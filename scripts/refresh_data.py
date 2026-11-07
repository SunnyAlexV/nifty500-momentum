"""Daily refresh used by GitHub Actions: update prices from NSE, refresh the Nifty 500 list
and corporate actions, then rewrite the snapshot so the deployed app always has fresh data."""
import sys
from datetime import date, timedelta
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from n500.db import connect, ROOT
from n500.update import update_prices
from n500.nse import nifty500_constituents, split_bonus_actions

con = connect()
print("new days:", update_prices(con))
cache = ROOT / "data" / "cache"; cache.mkdir(parents=True, exist_ok=True)
nifty500_constituents().to_parquet(cache / "nifty500.parquet")
split_bonus_actions(date.today() - timedelta(days=430), date.today() + timedelta(days=1)).to_parquet(cache / "actions.parquet")
con.close()
import runpy; sys.argv = ["make_snapshot.py", "300"]
runpy.run_path(str(ROOT / "scripts" / "make_snapshot.py"))
