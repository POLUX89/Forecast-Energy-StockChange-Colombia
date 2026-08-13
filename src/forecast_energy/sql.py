"""Run the analytical SQL in ``sql/`` against the parquet layers with DuckDB.

DuckDB reads parquet directly, so there is no database to provision and no copy
of the data to keep in sync: the same files the ingestion writes are the tables
the queries read.

Two views are registered before a query runs:

- ``raw``       — every record as fetched, all settlement versions kept.
- ``canonical`` — one row per hour, most mature version, built by
  ``sql/01_canonical.sql``. Derived from ``raw`` rather than read from
  ``data/processed/``, so a query never sees a canonical table that is staler
  than the raw files it sits on.

Usage:
    python -m forecast_energy.sql 04_gaps
    python -m forecast_energy.sql 05_volatility --limit 20
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from forecast_energy.ingest import RAW_DIR, REPO_ROOT

SQL_DIR = REPO_ROOT / "sql"
CANONICAL_QUERY = "01_canonical"


def query_path(name: str) -> Path:
    """Resolve a query name ("04_gaps" or "04_gaps.sql") to its file."""
    path = SQL_DIR / (name if name.endswith(".sql") else f"{name}.sql")
    if not path.exists():
        available = ", ".join(sorted(p.stem for p in SQL_DIR.glob("*.sql")))
        raise FileNotFoundError(f"No query {path.name} in {SQL_DIR}. Available: {available}")
    return path


def read_query(name: str) -> str:
    return query_path(name).read_text(encoding="utf-8")


def _connect(raw_dir: Path):
    """Open an in-memory DuckDB with the `raw` and `canonical` views registered."""
    import duckdb  # imported lazily: the `sql` dependency group is optional

    files = sorted(raw_dir.glob("ec6945_*.parquet"))
    if not files:
        raise FileNotFoundError(f"No raw files in {raw_dir}; run a backfill first")

    con = duckdb.connect()
    # The path list is inlined rather than bound: a view definition has to stay
    # re-evaluatable, so DuckDB does not keep prepared parameters inside one.
    # Paths come from a glob of our own data directory, and single quotes are
    # escaped for the pathological case of a quote in a directory name.
    paths = ", ".join("'" + str(f).replace("'", "''") + "'" for f in files)
    # union_by_name tolerates a column added to later yearly files.
    con.execute(f"CREATE VIEW raw AS SELECT * FROM read_parquet([{paths}], union_by_name = true)")
    con.execute(f"CREATE VIEW canonical AS {read_query(CANONICAL_QUERY).rstrip().rstrip(';')}")
    return con


def run_query(name: str, raw_dir: Path = RAW_DIR) -> pd.DataFrame:
    """Execute a query from ``sql/`` and return its result as a DataFrame."""
    con = _connect(raw_dir)
    try:
        return con.execute(read_query(name)).df()
    finally:
        con.close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("query", help="query name, e.g. 04_gaps")
    parser.add_argument("--limit", type=int, default=25, help="rows to print (0 for all)")
    args = parser.parse_args(argv)

    df = run_query(args.query)
    with pd.option_context("display.max_columns", None, "display.width", 200):
        print(df if args.limit == 0 else df.head(args.limit))
    print(f"\n[{len(df)} rows]")


if __name__ == "__main__":
    main()
