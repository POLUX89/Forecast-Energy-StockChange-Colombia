# Datasheet — Colombian hourly electricity spot price

Following the structure proposed in [*Datasheets for Datasets*](https://arxiv.org/abs/1803.09010)
(Gebru et al.). Figures are as of **2026-08-13**; the dataset grows daily, so
counts and the end date move while the start date and the schema do not.

---

## Motivation

**Why was the dataset created?** To support time-series forecasting of the
Colombian wholesale electricity spot price (*precio de bolsa*) on public data,
with a pipeline that anyone can re-run. XM publishes the underlying data through
the SIMEM open-data portal, but only through an API that caps requests at one
calendar month and returns every settlement revision as a separate record. This
repository turns that into a continuous, analysis-ready hourly series while
keeping the revision history intact.

**Who created it?** The upstream data is produced by **XM S.A. E.S.P.**, the
operator of Colombia's National Interconnected System and administrator of the
wholesale market, and published on [SIMEM](https://www.simem.co) under CREG
Resolution 101 018 of 2022. This repository — the ingestion code, the derived
canonical table and this datasheet — is maintained by Daniel Felipe Sacristan.

---

## Composition

**What do the instances represent?** One instance is one **hour** of the
Colombian wholesale market, with the price it settled at. There is no personal
or sensitive data of any kind: instances describe a national market, not people.

The data lives in two layers.

### Canonical layer — `data/processed/spot_price_hourly.parquet`

One row per hour, carrying the most mature settlement version available for that
hour.

| Column | Type | Meaning |
| --- | --- | --- |
| `datetime` | `datetime64[ns]` | Hour of the market, naive local time (America/Bogota) |
| `pb_nal` | `float64` | National spot price, COP/kWh |
| `pb_int` | `float64` | International spot price, COP/kWh |
| `pb_tie` | `float64` | TIE spot price (international transactions), COP/kWh |

- **101,760 rows**, covering **2015-01-01 00:00** to **2026-08-10 23:00**
- **No gaps and no missing values.** Every hour in the range is present and all
  three prices are populated. Verified on every run and by `sql/04_gaps.sql`,
  which returns zero rows on a healthy table
- 1.73 MB on disk

Distribution of `pb_nal` over the full history (COP/kWh):

| min | p1 | p25 | median | p75 | p99 | max | mean | sd |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 46.8 | 64.6 | 116.5 | 192.1 | 358.2 | 1,271.5 | 2,821.5 | 299.1 | 288.6 |

The series is strongly right-skewed: the mean sits well above the median because
of drought-driven price spikes. Annual means make the regimes visible — 378
(2015) and 300 (2016) during one El Niño, a 106 trough in 2017, then 558 (2023)
and 676 (2024) during the next. **These are nominal COP and are not adjusted for
inflation**, so part of the long-run drift is monetary rather than physical.

### Raw layer — `data/raw/ec6945_<year>.parquet`

Every record exactly as published, all settlement versions kept — **1,110,432
rows** across 12 yearly files, 2.9 MB total. Columns are those of SIMEM dataset
`EC6945`: `CodigoVariable` (`PB_Nal`, `PB_Int`, `PB_Tie`), `FechaHora`,
`CodigoDuracion` (always `PT1H`), `UnidadMedida` (always `COP/kWh`), `Version`,
and `Valor`.

**Settlement versions are the defining feature of this dataset.** XM re-publishes
the same hour repeatedly as its settlement matures. Eleven version codes appear:
`TX1`, `TX2`, `TXR`, `TXF`, `TX3`, `TXA`, `TX4`, `TX5`, `TX6`, `TX7`, `TX8`.
Their maturity order is **not** lexicographic and not obvious from the names:

```
TX1 < TX2 < TXR < TXF < TX3 ≈ TXA < TX4 < TX5 < TX6 < TX7 < TX8
```

`TXR` (re-settlement) and `TXF` (invoice) close the regular monthly cycle around
days 5 and 10 of the following month, and `TX3` onwards are **adjustments**
published months later — so `TXF`, despite meaning "final invoice", is not the
definitive value. This order was verified against SIMEM dataset `24914F`
(*Versiones de factura para la liquidación mensual*), which carries an explicit
publication date and an `EsMaximaVersion` flag per month. Ordering by name
instead would leave roughly 47% of hours holding a stale value.

---

## Collection process

**How was the data acquired?** Downloaded from the SIMEM public REST API
(`https://www.simem.co/backend-files/api/PublicData`, dataset `ec6945`), which
requires no authentication. It is observational data reported by the market
operator — not measured, sampled or inferred by this project.

**Is it a sample?** No. It is the complete published history for this dataset
from 2015-01-01 onwards. SIMEM returns nothing before that date, so 2015 is a
limit of the source, not a choice. Deeper history exists in XM's older Sinergox
API (metric `PrecBolsNaci`) and is not used here.

**Mechanics and timing.** The API rejects any range longer than one calendar
month, so every fetch is chunked by month; the initial backfill was about 135
requests. A GitHub Actions workflow then refreshes the data every day at 10:00
UTC (05:00 America/Bogota), an hour after SIMEM's own daily update, and fetches
a **trailing seven-day window** rather than a single day. The window covers two
things at once: the roughly **three-day publication lag** — a price for day *D*
first appears around *D+3* — and any hour re-published under a newer settlement
version, with `TX2` typically arriving about two days after `TX1`.

---

## Preprocessing and cleaning

**What was done to the raw data?** Nothing is discarded, corrected or imputed.
Records are stored as published, normalized only to a fixed column order and
stable dtypes. The canonical layer is a pure derivation: for each
(variable, hour) it selects the row with the highest maturity rank, then pivots
to wide form. Unmapped version codes rank last, so a code not yet understood can
never displace a known one; it is kept in the raw layer and logged as a warning.

**Is the raw data preserved?** Yes, and deliberately. Keeping every version is
what makes the ingestion idempotent — re-fetching any window can never duplicate
or overwrite — and it is what allows the revision behaviour to be studied at all.
Combined with the daily commits, the git history is accumulating a
**point-in-time record** of what was known on each date.

The canonical table can be rebuilt from the raw layer at any time, by
`ingest.build_canonical()` or by the SQL twin in `sql/01_canonical.sql`. A parity
test in CI asserts the two agree.

---

## Uses

**What is it for?** Hourly and day-ahead price forecasting, seasonality and
volatility analysis, and studying how settlement revisions affect published
prices. Colombia's ~70% hydro generation makes the series regime-dependent and
spike-prone, which is what makes it interesting and hard.

### Limitations that should shape any use

- **Revisions are large.** Of the 49,128 hours where both a first (`TX1`) and a
  definitive value exist, **88.4% are revised by more than 1 COP/kWh** and
  **20.5% by more than 5%**, with a maximum observed move of 4,734 COP/kWh.
  Training on definitive values and evaluating against them measures something a
  forecaster could not have known in real time.
- **Point-in-time reconstruction is impossible before 2021.** SIMEM exposes only
  **one version per hour for 2015–2020** (already-settled values), against 4–8
  versions per hour from 2021 on. Any vintage-aware experiment is therefore
  confined to 2021 onwards; the earlier years are usable as a price series but
  not as a record of what was known when.
- **The three-day lag redefines "day-ahead".** With public data alone, the most
  recent priced hour is about three days old, so a model has no access to
  yesterday's price. A classic day-ahead setup is not reproducible from this
  source.
- **Prices are nominal**, not deflated.
- **`TXA` is not fully understood.** It occupies the first adjustment slot and
  never coexists with `TX3`, which is why they share a rank, but no documentation
  confirming their equivalence was found.
- **One publication exceeds its regulatory window.** CREG Resolution 084 of 2007
  allows adjustments within five months of first invoicing, yet the `TX6` for
  December 2023 was published in November 2024, about eleven months later. The
  reason is not documented here.

**What should it not be used for?** Anything requiring the legally authoritative
value — settlement, billing, disputes. The copy here is a snapshot; the
authoritative source is always the SIMEM API.

---

## Distribution

The data is redistributed inside this public GitHub repository, unmodified in
substance, with attribution. The **code** is MIT-licensed; **the data is not
covered by that licence**. It belongs to XM S.A. E.S.P. and is published as open
data under CREG Resolution 101 018 of 2022. Anyone reusing it should credit
XM/SIMEM rather than this repository, and check SIMEM's current terms, which can
change.

---

## Maintenance

Maintained by the repository owner through automation: the daily workflow
updates the data unattended, and its status is visible from the badge in the
README. Failures are loud by design — a red badge means the data is aging.

The pipeline recovers from source outages on its own. On **9–10 August 2026** the
SIMEM API returned HTTP 400 for two days (`Database 'sql-simem-prod-02...' is
not accessible due to Azure Key Vault critical error`); the workflow failed both
days, and the first successful run afterwards refilled all three missing days
automatically. That self-healing holds for outages shorter than the seven-day
window; a longer one needs a manual wider run
(`python -m forecast_energy.ingest --days 21`).

Older data is never deleted. Revisions are applied by upsert, and the git history
retains every prior state, so any past version of the dataset can be recovered by
checking out the corresponding commit.
