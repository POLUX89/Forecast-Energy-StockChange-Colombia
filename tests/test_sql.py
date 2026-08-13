"""Unit tests for the SQL layer. No network access needed.

The point of this file is `TestCanonicalParity`: the canonical table has two
implementations — pandas in `ingest.build_canonical()` and SQL in
`sql/01_canonical.sql` — and these tests assert they produce the same result,
including the settlement-version edge cases that motivated the ranking table.
Without them the SQL would be documentation; with them it is verified code.
"""

import pandas as pd
import pytest
from test_ingest import make_records

from forecast_energy import ingest, sql

pytest.importorskip("duckdb", reason="install the optional `sql` dependency group")


@pytest.fixture
def raw_dir(tmp_path):
    """A raw layer holding two hours, each published under several versions."""
    ingest.upsert_raw(
        make_records(
            [
                ("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0),
                ("PB_Nal", "2024-01-01 00:00:00", "TXF", 110.0),
                ("PB_Int", "2024-01-01 00:00:00", "TX1", 200.0),
                ("PB_Tie", "2024-01-01 00:00:00", "TX2", 300.0),
                ("PB_Nal", "2024-01-01 01:00:00", "TX1", 120.0),
                ("PB_Int", "2024-01-01 01:00:00", "TX2", 220.0),
                ("PB_Tie", "2024-01-01 01:00:00", "TX1", 320.0),
            ]
        ),
        raw_dir=tmp_path,
    )
    return tmp_path


def canonical_both_ways(raw_dir, tmp_path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The same table built by pandas and by SQL, aligned for comparison."""
    pandas_df = ingest.build_canonical(raw_dir=raw_dir, out_path=tmp_path / "canonical.parquet")
    sql_df = sql.run_query("01_canonical", raw_dir=raw_dir)
    # build_canonical drops columns for variables absent from the window;
    # the SQL emits all three by design, so compare on the shared ones.
    columns = ["datetime", *[c for c in ("pb_nal", "pb_int", "pb_tie") if c in pandas_df.columns]]
    return (
        pandas_df[columns].reset_index(drop=True),
        sql_df[columns].reset_index(drop=True),
    )


class TestCanonicalParity:
    def test_matches_pandas(self, raw_dir, tmp_path):
        pandas_df, sql_df = canonical_both_ways(raw_dir, tmp_path)
        pd.testing.assert_frame_equal(sql_df, pandas_df, check_dtype=False)

    def test_adjustment_beats_invoice_version(self, tmp_path):
        # TX3 is an adjustment published after the TXF invoice, so it wins even
        # though "TXF" sorts later alphabetically. This is the bug the ranking
        # table exists to prevent, checked here on the SQL path.
        raw = tmp_path / "raw"
        raw.mkdir()
        ingest.upsert_raw(
            make_records(
                [
                    ("PB_Nal", "2024-01-01 00:00:00", "TXF", 110.0),
                    ("PB_Nal", "2024-01-01 00:00:00", "TX3", 115.0),
                ]
            ),
            raw_dir=raw,
        )
        pandas_df, sql_df = canonical_both_ways(raw, tmp_path)
        assert sql_df.loc[0, "pb_nal"] == 115.0
        pd.testing.assert_frame_equal(sql_df, pandas_df, check_dtype=False)

    def test_unknown_version_never_wins(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        ingest.upsert_raw(
            make_records(
                [
                    ("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0),
                    ("PB_Nal", "2024-01-01 00:00:00", "ZZ9", 999.0),
                ]
            ),
            raw_dir=raw,
        )
        pandas_df, sql_df = canonical_both_ways(raw, tmp_path)
        assert sql_df.loc[0, "pb_nal"] == 100.0
        pd.testing.assert_frame_equal(sql_df, pandas_df, check_dtype=False)


class TestAnalyticalQueries:
    def test_revisions_reports_the_full_chain(self, raw_dir):
        df = sql.run_query("02_revisions", raw_dir=raw_dir)
        row = df[df["variable"] == "PB_Nal"].iloc[0]
        # The 00:00 hour went TX1 100 -> TXF 110, the 01:00 hour has one version.
        assert row["avg_versions_per_hour"] == 1.5
        assert row["avg_abs_revision_cop"] == 5.0
        assert row["pct_revised_over_1_cop"] == 50.0

    def test_gaps_is_empty_on_a_continuous_series(self, raw_dir):
        assert sql.run_query("04_gaps", raw_dir=raw_dir).empty

    def test_gaps_names_the_missing_hour(self, tmp_path):
        raw = tmp_path / "raw"
        raw.mkdir()
        ingest.upsert_raw(
            make_records(
                [
                    ("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0),
                    ("PB_Nal", "2024-01-01 02:00:00", "TX1", 102.0),  # 01:00 missing
                ]
            ),
            raw_dir=raw,
        )
        gaps = sql.run_query("04_gaps", raw_dir=raw)
        assert len(gaps) == 1
        assert gaps.loc[0, "missing_hour"] == pd.Timestamp("2024-01-01 01:00:00")

    @pytest.mark.parametrize("name", ["03_seasonality", "05_volatility"])
    def test_query_runs_and_returns_rows(self, name, raw_dir):
        assert not sql.run_query(name, raw_dir=raw_dir).empty


class TestQueryResolution:
    def test_accepts_name_with_or_without_extension(self):
        assert sql.query_path("04_gaps") == sql.query_path("04_gaps.sql")

    def test_unknown_query_lists_the_available_ones(self):
        with pytest.raises(FileNotFoundError, match="Available:"):
            sql.query_path("does_not_exist")
