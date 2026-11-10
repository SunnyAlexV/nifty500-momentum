# Nifty 500 · Volatility-Adjusted Momentum Z-Scores

A Streamlit app that ranks the current Nifty 500 by cross-sectional, volatility-adjusted
momentum. Every refresh pulls any new NSE bhavcopy, the latest Nifty 500 list and recent
split / bonus / demerger actions, then recomputes all scores from the latest close.

## Online

Deployed on Streamlit Community Cloud. A GitHub Action refreshes `data/days/` every weekday
evening (IST) so the hosted app always has the latest close.

## Run locally (Windows, Anaconda Prompt or PowerShell)

```
cd "C:\Users\Sunny\Desktop\MM\Poincare Quantitative\n500_momentum"
pip install -r requirements.txt
streamlit run app.py
```

The app opens at http://localhost:8501. **Refresh now** (sidebar) forces a fresh pull;
otherwise results are cached for 15 minutes. NSE publishes each day's bhavcopy in the
evening (IST), so during market hours the scores are as of the previous close.

## Signals

All returns are log returns of split/bonus-adjusted prices.

| Signal | Score S |
|---|---|
| Composite 6M + 12M (default) | std[ ½ (z(R6/σ) + z(R12/σ)) ] |
| Composite 6-1M + 12-1M | same, with the last 21 days skipped |
| 6M / σ (original) | R6 / σ |
| 6-1M / σ | R(6-1) / σ |
| 12-1M / σ | R(12-1) / σ |

6M = 126 trading days, 12M = 252, σ = annualised stdev of the last 252 daily log returns.
Z = (S − mean) / sd across eligible stocks. Eligible: ≥ 90% of the last 252 days priced,
traded in the last 5 days, 6-month median daily traded value above the threshold (₹5 cr default).

## My portfolio tab

Turns the ranking into this month's orders. Inputs: total portfolio value, N, exit rank
(default 60), weighting (inverse-volatility or equal), industry cap (default 35% of names),
max weight per stock (default 10%) and current holdings (`SYMBOL QTY` per line).

1. Keep holdings ranked ≤ exit rank; sell the rest (and anything no longer eligible).
2. Fill empty slots from the top of the ranking, skipping full industries and stocks whose
   single share costs more than their allocation.
3. Weights wᵢ ∝ 1/σᵢ, capped at the max weight (excess redistributed pro rata).
4. Whole shares: floor(value · wᵢ / priceᵢ); leftover cash is then spent one share at a time
   on the name furthest below its target, never above the cap.

## Price adjustments (see the *Data checks* tab)

1. Bonus a:b → factor (a+b)/b; split FV₁→FV₂ → factor FV₁/FV₂ (NSE corporate-actions feed).
   Bonuses in preference shares (NCRPS) are ignored.
2. Demerger ex-dates where NSE did not re-base the price (|move| > 35%) → that day set to missing.
3. A stock's listing day inside the window → dropped (reference is the IPO price).
4. Any other move > 35% is kept and listed for a manual check.

## Layout

```
app.py                 Streamlit app
src/n500/bhavcopy.py   NSE bhavcopy download + parse (legacy and UDiFF formats)
src/n500/update.py     incremental price update
src/n500/nse.py        Nifty 500 list, corporate actions, ratio parsing
src/n500/signals.py    history linking, adjusted returns, scores, Z-scores
src/n500/portfolio.py  selection, industry cap, inverse-vol weights, whole-share orders
scripts/               one-off full-history download and ingest (2010 onwards)
tests/                 pytest: data integrity + signal maths on synthetic data
data/days/             last 300 trading days, one parquet per day (committed)
data/cache/            last good Nifty 500 list + corporate actions (fallback)
data/n500.duckdb       local price database, built from data/days/ (not committed)
.github/workflows/     daily data refresh
```
