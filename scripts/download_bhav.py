"""Download all NSE CM bhavcopies between two dates (resumable, time-boxed).

usage: python scripts/download_bhav.py 2010-01-01 2026-11-06 [max_seconds] [workers]
Re-run until it prints 'complete'. 404 days are recorded as holidays.
"""
import sys, time, json
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
import requests
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from n500.bhavcopy import download_day, raw_path

ROOT = Path(__file__).resolve().parents[1] / "data" / "raw" / "bhav"
HOL = ROOT / "holidays.json"
import os
WEEKENDS = os.environ.get("WEEKENDS") == "1"

def main(a, b, max_s=1e9, workers=3):
    t0 = time.time()
    start, end = date.fromisoformat(a), date.fromisoformat(b)
    hol = set(json.loads(HOL.read_text())) if HOL.exists() else set()
    todo, d = [], start
    while d <= end:
        if (d.weekday() < 5 or WEEKENDS) and d.isoformat() not in hol and not raw_path(ROOT, d).exists():
            todo.append(d)
        d += timedelta(days=1)
    print(f"{len(todo)} days to fetch", flush=True)
    s = requests.Session(); n = {"ok": 0, "cached": 0, "holiday": 0}

    def job(day):
        st = download_day(day, ROOT, s); time.sleep(0.3); return day, st

    with ThreadPoolExecutor(workers) as ex:
        for i in range(0, len(todo), workers * 10):
            if time.time() - t0 > max_s:
                print("time budget reached", n, flush=True); break
            for day, st in ex.map(job, todo[i:i + workers * 10]):
                n[st] += 1
                if st == "holiday": hol.add(day.isoformat())
            HOL.write_text(json.dumps(sorted(hol)))
        else:
            print("complete", n, flush=True)

if __name__ == "__main__":
    a = sys.argv[1:]
    main(a[0], a[1], float(a[2]) if len(a) > 2 else 1e9, int(a[3]) if len(a) > 3 else 3)
