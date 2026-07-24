# Forecast-Energy-StockChange-Colombia

[![CI](https://github.com/POLUX89/Forecast-Energy-StockChange-Colombia/actions/workflows/ci.yml/badge.svg)](https://github.com/POLUX89/Forecast-Energy-StockChange-Colombia/actions/workflows/ci.yml)

Forecasting Colombia's hourly wholesale electricity spot price (*precio de bolsa*) using public data from SIMEM/XM, with a self-updating ingestion pipeline on GitHub Actions.

> Status: Phase 1 — repository & data ingestion. See [ROADMAP.md](ROADMAP.md) for the full plan.

## Data source

Hourly spot price dataset `EC6945` (*Precio de bolsa horario*: national, international and TIE prices, COP/kWh) from [SIMEM](https://www.simem.co), the Colombian wholesale energy market information system operated by XM S.A. E.S.P. Data is public and fetched through the SIMEM open API / [pydataxm](https://github.com/EquipoAnaliticaXM/API_XM).

Two properties of the source shape the whole pipeline:

- **Prices are revised.** XM re-publishes each hour under successive settlement
  versions. Their real order, verified against SIMEM's version calendar
  (dataset `24914F`), is `TX1 < TX2 < TXR < TXF < TX3 … TX8` — the `TX3`+
  *adjustments* are published months after the `TXF` invoice, so `TXF` is not
  the final word. Over 89% of hours are revised by more than 1 COP/kWh between
  the first and the definitive version.
- **Publication lags about three days.** The API refreshes daily around 04:00
  America/Bogota, but the most recent priced hours are roughly three days old,
  because the price comes from XM's daily settlement rather than a live reading.

## Data layout

| Path | Content |
| --- | --- |
| `data/raw/ec6945_<year>.parquet` | Every record as published, all settlement versions kept |
| `data/processed/spot_price_hourly.parquet` | One row per hour (`datetime`, `pb_nal`, `pb_int`, `pb_tie`), most mature version per hour |

The canonical table currently spans 2015-01-01 to 2026-07-20: 101,256 continuous
hourly observations with no gaps and no nulls.

```python
import pandas as pd

df = pd.read_parquet("data/processed/spot_price_hourly.parquet")
```

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

## Project structure

```
├── .github/workflows/   # CI, and the scheduled daily ingestion
├── data/
│   ├── raw/             # As-fetched data, all settlement versions
│   └── processed/       # Canonical analysis-ready table
├── docs/                # Decisions log and project docs
├── notebooks/           # EDA and experiments (Phase 2+)
├── src/forecast_energy/ # Package code: ingestion, features, models
└── tests/
```
