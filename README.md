# Forecast-Energy-StockChange-Colombia

Forecasting Colombia's hourly wholesale electricity spot price (*precio de bolsa*) using public data from SIMEM/XM, with a self-updating ingestion pipeline on GitHub Actions.

> Status: Phase 1 — repository & data ingestion. See [ROADMAP.md](ROADMAP.md) for the full plan.

## Data source

Hourly spot price dataset `EC6945` (*Precio de bolsa horario*: national, international and TIE prices, COP/kWh) from [SIMEM](https://www.simem.co), the Colombian wholesale energy market information system operated by XM S.A. E.S.P. Data is public and fetched through the SIMEM open API / [pydataxm](https://github.com/EquipoAnaliticaXM/API_XM).

## Project structure

```
├── .github/workflows/   # CI + scheduled daily ingestion (Phase 1)
├── data/
│   ├── raw/             # As-fetched data, versioned in git
│   └── processed/       # Canonical analysis-ready datasets
├── docs/                # Decisions log and project docs
├── notebooks/           # EDA and experiments (Phase 2+)
├── src/forecast_energy/ # Package code: ingestion, features, models
└── tests/
```

## Setup

Requires Python 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
uv sync                  # core dependencies (pydataxm)
uv sync --group eda      # + EDA stack (numpy, pandas, matplotlib, scipy, statsmodels)
uv sync --group mlops    # + MLOps stack (scikit-learn, mlflow)
uv sync --all-groups     # everything
```
