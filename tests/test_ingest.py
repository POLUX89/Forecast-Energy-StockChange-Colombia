"""Unit tests for the ingestion module. No network access needed."""

from datetime import date

import pandas as pd
import pytest

from forecast_energy import ingest


def make_records(rows: list[tuple[str, str, str, float]]) -> pd.DataFrame:
    """rows: (variable, iso_datetime, version, value)"""
    return ingest._normalize(
        pd.DataFrame(
            [
                {
                    "CodigoVariable": var,
                    "FechaHora": ts,
                    "CodigoDuracion": "PT1H",
                    "UnidadMedida": "COP/kWh",
                    "Version": version,
                    "Valor": value,
                }
                for var, ts, version, value in rows
            ]
        )
    )


class TestMonthChunks:
    def test_covers_range_without_gaps_or_overlaps(self):
        chunks = list(ingest.month_chunks(date(2015, 1, 15), date(2015, 3, 10)))
        assert chunks == [
            (date(2015, 1, 15), date(2015, 1, 31)),
            (date(2015, 2, 1), date(2015, 2, 28)),
            (date(2015, 3, 1), date(2015, 3, 10)),
        ]

    def test_single_day(self):
        assert list(ingest.month_chunks(date(2024, 6, 15), date(2024, 6, 15))) == [
            (date(2024, 6, 15), date(2024, 6, 15))
        ]

    def test_no_chunk_exceeds_one_calendar_month(self):
        for start, end in ingest.month_chunks(date(2015, 1, 1), date(2026, 7, 23)):
            assert start.month == end.month and start.year == end.year


class TestUpsertRaw:
    def test_idempotent(self, tmp_path):
        records = make_records([("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0)])
        assert ingest.upsert_raw(records, raw_dir=tmp_path) == 1
        assert ingest.upsert_raw(records, raw_dir=tmp_path) == 0
        stored = pd.read_parquet(tmp_path / "ec6945_2024.parquet")
        assert len(stored) == 1

    def test_new_version_is_added_and_old_kept(self, tmp_path):
        ingest.upsert_raw(make_records([("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0)]), raw_dir=tmp_path)
        ingest.upsert_raw(make_records([("PB_Nal", "2024-01-01 00:00:00", "TX2", 105.0)]), raw_dir=tmp_path)
        stored = pd.read_parquet(tmp_path / "ec6945_2024.parquet")
        assert sorted(stored["Version"]) == ["TX1", "TX2"]

    def test_splits_by_year(self, tmp_path):
        records = make_records(
            [
                ("PB_Nal", "2023-12-31 23:00:00", "TX1", 90.0),
                ("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0),
            ]
        )
        ingest.upsert_raw(records, raw_dir=tmp_path)
        assert (tmp_path / "ec6945_2023.parquet").exists()
        assert (tmp_path / "ec6945_2024.parquet").exists()


class TestBuildCanonical:
    def test_latest_version_wins(self, tmp_path):
        ingest.upsert_raw(
            make_records(
                [
                    ("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0),
                    ("PB_Nal", "2024-01-01 00:00:00", "TXR", 111.0),
                    ("PB_Int", "2024-01-01 00:00:00", "TX1", 200.0),
                ]
            ),
            raw_dir=tmp_path,
        )
        wide = ingest.build_canonical(raw_dir=tmp_path, out_path=tmp_path / "canonical.parquet")
        assert len(wide) == 1
        assert wide.loc[0, "pb_nal"] == 111.0
        assert wide.loc[0, "pb_int"] == 200.0

    def test_adjustment_beats_invoice_version(self, tmp_path):
        # TX3+ are adjustments published AFTER the TXF invoice (SIMEM 24914F),
        # so they must win over TXR/TXF despite the counterintuitive naming.
        ingest.upsert_raw(
            make_records(
                [
                    ("PB_Nal", "2024-01-01 00:00:00", "TXF", 100.0),
                    ("PB_Nal", "2024-01-01 00:00:00", "TX3", 111.0),
                    ("PB_Nal", "2024-01-02 00:00:00", "TX2", 200.0),
                    ("PB_Nal", "2024-01-02 00:00:00", "TXF", 222.0),
                ]
            ),
            raw_dir=tmp_path,
        )
        wide = ingest.build_canonical(raw_dir=tmp_path, out_path=tmp_path / "canonical.parquet")
        assert wide.loc[0, "pb_nal"] == 111.0  # adjustment TX3 beats invoice TXF
        assert wide.loc[1, "pb_nal"] == 222.0  # invoice TXF beats early TX2

    def test_unknown_version_never_wins(self, tmp_path):
        ingest.upsert_raw(
            make_records(
                [
                    ("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0),
                    ("PB_Nal", "2024-01-01 00:00:00", "TX9", 999.0),
                ]
            ),
            raw_dir=tmp_path,
        )
        wide = ingest.build_canonical(raw_dir=tmp_path, out_path=tmp_path / "canonical.parquet")
        assert wide.loc[0, "pb_nal"] == 100.0

    def test_rebuild_is_stable(self, tmp_path):
        ingest.upsert_raw(make_records([("PB_Nal", "2024-01-01 00:00:00", "TX1", 100.0)]), raw_dir=tmp_path)
        out = tmp_path / "canonical.parquet"
        first = ingest.build_canonical(raw_dir=tmp_path, out_path=out)
        mtime = out.stat().st_mtime_ns
        second = ingest.build_canonical(raw_dir=tmp_path, out_path=out)
        assert first.equals(second)
        assert out.stat().st_mtime_ns == mtime  # unchanged content is not rewritten

    def test_raises_without_raw_files(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            ingest.build_canonical(raw_dir=tmp_path, out_path=tmp_path / "canonical.parquet")
