# Forecast-Energy — Roadmap

End-to-end forecasting of Colombia's hourly wholesale electricity spot price (*precio de bolsa*), built on public SIMEM data (dataset `EC6945`), with a self-updating data pipeline on GitHub Actions.

Data source: [SIMEM](https://www.simem.co) / XM S.A. E.S.P. — public API, no auth. Attribution required in README.

---

## Phase 1 — Repository & data ingestion

**Goal:** a reproducible repo whose dataset updates itself every day with zero human intervention.

- [x] Git repo + scaffolding: `pyproject.toml`, dependency lock, tests skeleton
- [x] Ingestion module for the SIMEM API (`EC6945`), shared by backfill and daily runs:
  - requests chunked ≤ 1 month (hard API limit)
  - idempotent upsert keyed by `(CodigoVariable, FechaHora, Version)`
  - canonical view: most mature settlement version per hour, ordered by real
    publication date rather than by name
- [x] Backfill of the full history 2015 → today, as a `--backfill` flag on the
      same module; re-runs are idempotent instead of resumable, with retries,
      backoff and polite pacing between chunks
- [x] Daily GitHub Actions workflow (`schedule` 10:00 UTC = 05:00 COT): fetch a
      trailing 7-day window to capture settlement revisions, upsert, commit only
      when data changed
- [x] Storage: Parquet partitioned by year, normalized to a fixed column set and
      dtypes on every read and write
- [x] CI: test suite on every push and pull request, installing with a bare
      `uv sync` to prove ingestion needs no analysis dependencies
- [x] README: project description, data source & attribution, how to run locally
- [x] Linting and formatting (`ruff`) wired into CI as its own job

**Outcome:** 1,107,336 raw records backfilled into 101,256 continuous hourly
observations from 2015-01-01, with no gaps and no nulls. Two findings that
changed the design and are worth carrying forward: settlement versions do not
mature in name order (`TX3`+ are adjustments published *after* the `TXF`
invoice, and using the naive order left 47% of hours stale), and prices are
published with a lag of about three days.

**Done when:** the scheduled workflow has pushed fresh data ≥ 3 consecutive days
unattended. The write path is already proven end to end — a deliberately
removed two-day gap was detected, refilled and committed by the bot — but the
unattended streak starts with the first scheduled run on 2026-07-24.

## Phase 2 — Exploratory data analysis

**Goal:** understand the series well enough that every modeling decision is grounded in evidence — each finding ends in a written decision.

- [ ] Seasonality (24 h / 168 h / annual), trend, distribution, price spikes (El Niño regimes)
- [ ] Settlement-version study: how much do `TX` revisions actually change published values?
- [ ] Data quality report: gaps, duplicates, timezone sanity (Colombia has no DST)
- [ ] `docs/decisions.md`: target series (`PB_Nal`), canonical version rule,
      outlier policy, train/validation/test time splits, and two questions the
      ingestion work surfaced — what "next-day forecast" means when prices
      arrive three days late, and which settlement vintage belongs in the
      target versus in the features

**Done when:** `docs/decisions.md` exists and later phases reference it instead of re-deciding.

## Phase 3 — Models

**Goal:** beat the seasonal-naive baseline with at least one model, proven by honest backtesting.

- [ ] Baselines: seasonal naive (t−24 h, t−168 h) — the bar every model must clear
- [ ] Statistical: SARIMA (via `statsforecast` for tractability on hourly data)
- [ ] Gradient boosting: XGBoost and LightGBM on a shared lag/calendar feature pipeline
- [ ] Rolling-origin backtest harness; metrics: MAE, RMSE, plus per-hour-of-day breakdown
- [ ] Model comparison report → pick a champion

**Done when:** a champion model is selected with documented backtest evidence.

## Phase 4 — MLOps: continuous evaluation

**Goal:** the system forecasts every day and grades itself when actuals arrive.

- [ ] Extend the daily workflow: after ingest, generate next-day 24 h forecast and commit it
- [ ] Evaluation job: score yesterday's forecast against published actuals, append to a metrics history file
- [ ] Rolling performance tracking (e.g. 30-day MAE) to surface degradation
- [ ] Scheduled retraining (e.g. monthly) with versioned model artifacts
- [ ] CI: tests run on every PR

**Done when:** the forecast → actuals → metrics loop runs unattended and the metrics history grows daily.

## Phase 5 — GitHub Pages dashboard

**Goal:** a public page anyone can open to see the price, the current forecast, and how good the model has been.

- [ ] Static dashboard rebuilt by the daily workflow: recent prices, next-day forecast, error metrics over time, model comparison
- [ ] Deployed via GitHub Pages
- [ ] Portfolio-ready README with screenshots and links

**Done when:** the page reflects the latest data and forecast every day without manual steps.
