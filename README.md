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

## Querying the data with SQL

The parquet files are also queried directly with **DuckDB** — no server, no
migration, no second copy of the data to keep in sync. The queries live in
[`sql/`](sql/) and run through a thin wrapper that registers two views: `raw`
(every settlement version) and `canonical` (one row per hour).

```bash
uv sync --group sql
uv run python -m forecast_energy.sql 04_gaps
uv run python -m forecast_energy.sql 05_volatility --limit 20
```

| Query | What it answers |
| --- | --- |
| [`01_canonical.sql`](sql/01_canonical.sql) | Most mature settlement version per hour, pivoted wide — the SQL twin of `build_canonical()` |
| [`02_revisions.sql`](sql/02_revisions.sql) | How far a price still moves after first publication, by year |
| [`03_seasonality.sql`](sql/03_seasonality.sql) | Average price by hour of day × month, as a matrix |
| [`04_gaps.sql`](sql/04_gaps.sql) | Which hours are missing — returns zero rows on a healthy table |
| [`05_volatility.sql`](sql/05_volatility.sql) | Monthly dispersion, p10/p50/p90 and a 12-month rolling mean |

The canonical table has **two implementations that must agree**. The pandas one
is what the daily pipeline runs; the SQL one expresses the same rule as a window
function:

```python
# pandas — src/forecast_energy/ingest.py
df.sort_values("_rank").drop_duplicates(["CodigoVariable", "FechaHora"], keep="last")
```

```sql
-- SQL — sql/01_canonical.sql
QUALIFY ROW_NUMBER() OVER (
    PARTITION BY "CodigoVariable", "FechaHora"
    ORDER BY maturity DESC
) = 1
```

`tests/test_sql.py::TestCanonicalParity` asserts the two produce the same table,
including the edge cases that motivated the ranking: a `TX3` adjustment must beat
the `TXF` invoice, and an unknown version must never win. CI runs them on every
push, so the SQL is verified code rather than documentation.

## How the automation works

`.github/workflows/daily-ingest.yml` runs at 10:00 UTC (05:00 America/Bogota),
an hour after SIMEM publishes. It fetches a trailing seven-day window rather
than a single day — wide enough to pick up both the three-day lag and any hours
XM has re-published under a later settlement version — then commits the result
only if something actually changed. Quiet days finish green without an empty
commit, and data commits carry `[skip ci]` so they do not trigger the test
workflow.

`.github/workflows/ci.yml` runs on every push and pull request: linting and
formatting, the test suite with the `sql` group so the DuckDB parity tests run
rather than skip, and a third job that installs the core dependencies alone and
runs the ingestion CLI. That last job guards the environment the daily workflow
actually uses — an import that only resolves inside an optional group would
otherwise pass CI and break the 05:00 run.

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

## Dataset documentation

[DATASHEET.md](DATASHEET.md) documents the dataset itself, following
*Datasheets for Datasets*: what an instance is, how the data was collected, the
settlement-version semantics, and the limitations that should shape any use of
it — revision magnitude, the three-day lag, and the fact that vintages only
exist from 2021 onwards.

## License

The code in this repository is released under the [MIT License](LICENSE).

That licence covers the code only. The price data under `data/` belongs to
**XM S.A. E.S.P.** and is published on [SIMEM](https://www.simem.co) as open
data, under CREG Resolution 101 018 of 2022. It is redistributed here
unmodified in substance, with attribution, to make the analysis reproducible.
If you reuse it, credit XM/SIMEM as the source rather than this repository, and
check SIMEM's current terms — they can change, and the authoritative copy is
always the API.

## Project structure

```
├── .github/workflows/   # ci.yml (tests) and daily-ingest.yml (scheduled data refresh)
├── data/
│   ├── raw/             # As-fetched data, all settlement versions
│   └── processed/       # Canonical analysis-ready table
├── docs/                # Decisions log and project docs
├── notebooks/           # EDA and experiments (Phase 2+)
├── sql/                 # Analytical DuckDB queries over the parquet layers
├── src/forecast_energy/ # Package code: ingestion, SQL runner, features, models
└── tests/
```
