"""Ingest the SIMEM hourly spot price dataset (EC6945) into local parquet files.

Two layers:

- Raw: ``data/raw/ec6945_<year>.parquet`` — every record as fetched, all
  settlement versions kept.
- Canonical: ``data/processed/spot_price_hourly.parquet`` — one row per hour,
  wide columns (pb_nal, pb_int, pb_tie), using the most mature settlement
  version available for each hour.

The SIMEM API rejects ranges longer than one month, so every fetch is chunked
by calendar month. XM re-publishes the same hours under successive settlement
versions (TX1, TX2, then TXR/TXF at month+1, then adjustments TX3..TX8 months
later), so ingestion is an idempotent upsert keyed on (CodigoVariable,
FechaHora, Version): re-fetching any window never duplicates.

Timestamps are naive local time (America/Bogota, which has no DST).

Usage:
    python -m forecast_energy.ingest                  # update: last 7 days
    python -m forecast_energy.ingest --days 14        # update: last 14 days
    python -m forecast_energy.ingest --backfill       # full history since 2015
"""

from __future__ import annotations

import argparse
import io
import logging
import time
from collections.abc import Iterator
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
from pydataxm.pydatasimem import ReadSIMEM

logger = logging.getLogger(__name__)

DATASET_ID = "EC6945"
BOGOTA = ZoneInfo("America/Bogota")
DEFAULT_START = date(2015, 1, 1)

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "data" / "raw"
CANONICAL_PATH = REPO_ROOT / "data" / "processed" / "spot_price_hourly.parquet"

COLUMNS = ["CodigoVariable", "FechaHora", "CodigoDuracion", "UnidadMedida", "Version", "Valor"]
KEY = ["CodigoVariable", "FechaHora", "Version"]

# XM settlement cycle in publication order, verified against SIMEM dataset
# 24914F ("Versiones de factura para la liquidación mensual"): TX1/TX2 are the
# early settlements, TXR (re-settlement, ~day 5 of month+1) and TXF (invoice,
# ~day 10 of month+1) close the regular cycle, and TX3..TX8 are *adjustments*
# published months later — so TXF is NOT the most mature value. TXA appears in
# place of TX3 in some months (same adjustment slot; they never coexist).
# Unknown versions rank 0 so they can never displace a known one.
VERSION_RANK = {
    "TX1": 1,
    "TX2": 2,
    "TXR": 3,
    "TXF": 4,
    "TX3": 5,
    "TXA": 5,
    "TX4": 6,
    "TX5": 7,
    "TX6": 8,
    "TX7": 9,
    "TX8": 10,
}

WIDE_NAMES = {"PB_Nal": "pb_nal", "PB_Int": "pb_int", "PB_Tie": "pb_tie"}

# ReadSIMEM downloads dataset metadata on construction (~15 s), so one client
# is built lazily and reused across chunks via set_dates().
_CLIENT: ReadSIMEM | None = None


def _client(start: date, end: date) -> ReadSIMEM:
    global _CLIENT
    with redirect_stdout(io.StringIO()):  # ReadSIMEM prints progress banners
        if _CLIENT is None:
            _CLIENT = ReadSIMEM(DATASET_ID, start.isoformat(), end.isoformat())
        else:
            _CLIENT.set_dates(start.isoformat(), end.isoformat())
    return _CLIENT


def today_bogota() -> date:
    return datetime.now(BOGOTA).date()


def month_chunks(start: date, end: date) -> Iterator[tuple[date, date]]:
    """Split [start, end] into calendar-month chunks the API accepts."""
    current = start
    while current <= end:
        next_month = (current.replace(day=1) + timedelta(days=32)).replace(day=1)
        yield current, min(end, next_month - timedelta(days=1))
        current = next_month


def _version_rank(versions: pd.Series) -> pd.Series:
    rank = versions.map(VERSION_RANK)
    unknown = versions[rank.isna()].unique()
    if len(unknown):
        logger.warning(
            "Unknown settlement versions %s: kept in raw, never preferred in canonical",
            sorted(unknown),
        )
    return rank.fillna(0).astype("int64")


def _normalize(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        df = pd.DataFrame(columns=COLUMNS)
    df = df.reindex(columns=COLUMNS).copy()
    df["FechaHora"] = pd.to_datetime(df["FechaHora"]).astype("datetime64[ns]")
    df["Valor"] = df["Valor"].astype("float64")
    for col in ("CodigoVariable", "CodigoDuracion", "UnidadMedida", "Version"):
        df[col] = df[col].astype("string")
    return df


def _sort_key(df: pd.DataFrame) -> pd.DataFrame:
    out = df.assign(_rank=_version_rank(df["Version"]))
    out = out.sort_values(["FechaHora", "CodigoVariable", "_rank", "Version"])
    return out.drop(columns="_rank").reset_index(drop=True)


def _fetch_chunk(start: date, end: date, retries: int) -> pd.DataFrame:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            client = _client(start, end)
            with redirect_stdout(io.StringIO()):
                df = client.main()
            return _normalize(df)
        except TypeError as exc:
            if "no data" in str(exc).lower():
                logger.info("No data for %s..%s (empty period)", start, end)
                return _normalize(pd.DataFrame())
            last_error = exc
        except Exception as exc:  # network / API hiccups
            last_error = exc
        logger.warning("Fetch %s..%s failed (attempt %d/%d): %s", start, end, attempt, retries, last_error)
        time.sleep(2 * attempt)
    raise RuntimeError(f"Could not fetch {start}..{end} after {retries} attempts") from last_error


def fetch_range(start: date, end: date, *, retries: int = 3, pause: float = 0.4) -> pd.DataFrame:
    """Fetch [start, end] in monthly chunks, returning normalized records."""
    frames = []
    for chunk_start, chunk_end in month_chunks(start, end):
        logger.info("Fetching %s..%s", chunk_start, chunk_end)
        frames.append(_fetch_chunk(chunk_start, chunk_end, retries))
        time.sleep(pause)
    if not frames:
        return _normalize(pd.DataFrame())
    return pd.concat(frames, ignore_index=True)


def upsert_raw(new: pd.DataFrame, raw_dir: Path = RAW_DIR) -> int:
    """Merge records into per-year raw parquet files; new records win on key.

    Returns the net number of new rows. Files are only rewritten when their
    content actually changes, so unchanged runs leave the git tree clean.
    """
    if new.empty:
        return 0
    raw_dir.mkdir(parents=True, exist_ok=True)
    added = 0
    for year, group in new.groupby(new["FechaHora"].dt.year):
        path = raw_dir / f"ec6945_{year}.parquet"
        if path.exists():
            existing = _normalize(pd.read_parquet(path))
            merged = pd.concat([existing, group], ignore_index=True)
        else:
            existing = None
            merged = group
        merged = _sort_key(merged.drop_duplicates(KEY, keep="last"))
        if existing is not None and merged.equals(_sort_key(existing)):
            logger.info("%s unchanged", path.name)
            continue
        merged.to_parquet(path, index=False)
        new_rows = len(merged) - (len(existing) if existing is not None else 0)
        added += new_rows
        logger.info("%s: %+d rows (total %d)", path.name, new_rows, len(merged))
    return added


def build_canonical(raw_dir: Path = RAW_DIR, out_path: Path = CANONICAL_PATH) -> pd.DataFrame:
    """Rebuild the wide hourly table keeping the most mature version per hour."""
    files = sorted(raw_dir.glob("ec6945_*.parquet"))
    if not files:
        raise FileNotFoundError(f"No raw files in {raw_dir}; run a backfill first")
    df = pd.concat([_normalize(pd.read_parquet(f)) for f in files], ignore_index=True)
    df = df.assign(_rank=_version_rank(df["Version"]))
    df = df.sort_values("_rank").drop_duplicates(["CodigoVariable", "FechaHora"], keep="last")
    wide = (
        df.pivot(index="FechaHora", columns="CodigoVariable", values="Valor")
        .rename(columns=WIDE_NAMES)
        .sort_index()
        .reset_index()
        .rename(columns={"FechaHora": "datetime"})
    )
    wide.columns.name = None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        current = pd.read_parquet(out_path)
        current["datetime"] = current["datetime"].astype("datetime64[ns]")
        if wide.equals(current):
            logger.info("Canonical table unchanged (%d hours)", len(wide))
            return wide
    wide.to_parquet(out_path, index=False)
    logger.info("Canonical table written: %d hours, %s..%s", len(wide), wide["datetime"].min(), wide["datetime"].max())
    return wide


def run(start: date, end: date) -> None:
    records = fetch_range(start, end)
    added = upsert_raw(records)
    logger.info("Fetched %d records, %+d new rows in raw", len(records), added)
    build_canonical()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ingest SIMEM hourly spot prices (EC6945)")
    parser.add_argument("--backfill", action="store_true", help="fetch full history instead of a recent window")
    parser.add_argument("--start", type=date.fromisoformat, default=DEFAULT_START, help="backfill start date")
    parser.add_argument("--end", type=date.fromisoformat, default=None, help="last date to fetch (default: today)")
    parser.add_argument("--days", type=int, default=7, help="trailing window size for regular updates")
    args = parser.parse_args(argv)

    # pydataxm logs through the root logger; keep root at WARNING so only this
    # package's INFO lines reach the output.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("forecast_energy").setLevel(logging.INFO)

    end = args.end or today_bogota()
    start = args.start if args.backfill else end - timedelta(days=args.days)
    run(start, end)


if __name__ == "__main__":
    main()
