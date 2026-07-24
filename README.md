# Forecast-Energy-StockChange-Colombia

[![CI](https://github.com/POLUX89/Forecast-Energy-StockChange-Colombia/actions/workflows/ci.yml/badge.svg)](https://github.com/POLUX89/Forecast-Energy-StockChange-Colombia/actions/workflows/ci.yml)
[![Daily ingest](https://github.com/POLUX89/Forecast-Energy-StockChange-Colombia/actions/workflows/daily-ingest.yml/badge.svg)](https://github.com/POLUX89/Forecast-Energy-StockChange-Colombia/actions/workflows/daily-ingest.yml)

Forecasting Colombia's hourly wholesale electricity spot price (*precio de bolsa*) using public data from SIMEM/XM. The dataset in this repository updates itself every morning through GitHub Actions, with no server and no manual step.

> Status: Phase 1 complete — the repository, the ingestion pipeline and the
> scheduled workflow are in place. See [ROADMAP.md](ROADMAP.md) for the five
> phases and what comes next.

## Data source

Hourly spot price dataset `EC6945` (*Precio de bolsa horario*: national, international and TIE prices, COP/kWh) from [SIMEM](https://www.simem.co), the Colombian wholesale energy market information system operated by XM S.A. E.S.P. Data is public and fetched through the SIMEM open API / [pydataxm](https://github.com/EquipoAnaliticaXM/API_XM).

Three properties of the source shape the whole pipeline:

- **Prices are revised.** XM re-publishes each hour under successive settlement
  versions. Their real order, verified against SIMEM's version calendar
  (dataset `24914F`), is `TX1 < TX2 < TXR < TXF < TX3 … TX8` — the `TX3`+
  *adjustments* are published months after the `TXF` invoice, so `TXF` is not
  the final word. Over 89% of hours are revised by more than 1 COP/kWh between
  the first version and the definitive one, and 21% by more than 5%.
- **Publication lags about three days.** The API refreshes daily around 04:00
  America/Bogota, but the most recent priced hours are roughly three days old,
  because the price comes from XM's daily settlement rather than a live reading.
- **Requests are capped at one calendar month.** Longer ranges are rejected, so
  every fetch is chunked by month.

## Data layout

| Path | Content |
| --- | --- |
| `data/raw/ec6945_<year>.parquet` | Every record as published, all settlement versions kept |
| `data/processed/spot_price_hourly.parquet` | One row per hour (`datetime`, `pb_nal`, `pb_int`, `pb_tie`), most mature version per hour |

The canonical table starts on 2015-01-01 and is extended daily. The initial
backfill produced 1,107,336 raw records condensed into 101,256 continuous hourly
observations, with no gaps and no nulls.

Keeping every version in the raw layer is deliberate: it makes the pipeline
idempotent, and combined with the daily commits it accumulates a point-in-time
record of what was known on each date — useful later for honest backtesting.

```python
import pandas as pd

df = pd.read_parquet("data/processed/spot_price_hourly.parquet")
```

## How the automation works

`.github/workflows/daily-ingest.yml` runs at 10:00 UTC (05:00 America/Bogota),
an hour after SIMEM publishes. It fetches a trailing seven-day window rather
than a single day — wide enough to pick up both the three-day lag and any hours
XM has re-published under a later settlement version — then commits the result
only if something actually changed. Quiet days finish green without an empty
commit, and data commits carry `[skip ci]` so they do not trigger the test
workflow.

`.github/workflows/ci.yml` runs the test suite on every push and pull request.
It installs with a bare `uv sync`, which also verifies that ingestion works
without the `eda` and `mlops` dependency groups.

## Usage

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # core dependencies (ingestion runs with just these)
uv sync --group eda      # + EDA stack (numpy, matplotlib, scipy, statsmodels)
uv sync --group mlops    # + MLOps stack (scikit-learn, mlflow)
uv sync --all-groups     # everything
```

Ingestion is idempotent: re-running any window never duplicates records, and
files are only rewritten when their content actually changes.

```bash
uv run python -m forecast_energy.ingest              # refresh the last 7 days
uv run python -m forecast_energy.ingest --days 30    # wider trailing window
uv run python -m forecast_energy.ingest --backfill   # full history since 2015
uv run pytest tests/                                 # test suite (no network needed)
```

Linting and formatting use [ruff](https://docs.astral.sh/ruff/); CI fails on
either. Both commands are safe to run repeatedly.

```bash
uv run ruff check --fix .   # lint, applying the safe fixes
uv run ruff format .        # format
```

## Project structure

```
├── .github/workflows/   # ci.yml (tests) and daily-ingest.yml (scheduled data refresh)
├── data/
│   ├── raw/             # As-fetched data, all settlement versions
│   └── processed/       # Canonical analysis-ready table
├── docs/                # Decisions log and project docs
├── notebooks/           # EDA and experiments (Phase 2+)
├── src/forecast_energy/ # Package code: ingestion, features, models
└── tests/
```
