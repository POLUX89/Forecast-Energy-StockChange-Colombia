-- Canonical hourly table: one row per hour, using the most mature settlement
-- version available for each hour.
--
-- This is the SQL twin of `ingest.build_canonical()`. Both implementations are
-- kept and `tests/test_sql.py::test_canonical_parity` asserts they agree, so
-- the query is verified code rather than documentation.
--
-- Why a ranking table instead of MAX(Version): XM's versions do not mature in
-- lexicographic order. TX3..TX8 are *adjustments* published months after the
-- TXF invoice, so ordering by name would treat TXF as final and leave 47% of
-- hours stale. Unknown versions rank 0 and can never displace a known one,
-- mirroring `_version_rank()` in ingest.py.
--
-- Expects a `raw` view over data/raw/ec6945_*.parquet (see src/forecast_energy/sql.py).

WITH version_rank(version, maturity) AS (
    VALUES ('TX1', 1), ('TX2', 2), ('TXR', 3), ('TXF', 4),
           ('TX3', 5), ('TXA', 5),                            -- same adjustment slot; never coexist
           ('TX4', 6), ('TX5', 7), ('TX6', 8), ('TX7', 9), ('TX8', 10)
),

ranked AS (
    SELECT
        r."CodigoVariable",
        r."FechaHora",
        r."Valor",
        COALESCE(v.maturity, 0) AS maturity
    FROM raw AS r
    LEFT JOIN version_rank AS v ON r."Version" = v.version
),

-- QUALIFY filters on the window result without a nested subquery: keep only the
-- most mature row per (variable, hour). The upsert key makes (variable, hour,
-- version) unique and TX3/TXA never coexist, so the ranking has no ties.
most_mature AS (
    SELECT "CodigoVariable", "FechaHora", "Valor"
    FROM ranked
    QUALIFY ROW_NUMBER() OVER (
        PARTITION BY "CodigoVariable", "FechaHora"
        ORDER BY maturity DESC
    ) = 1
)

-- Long to wide. Conditional aggregation rather than PIVOT so the output columns
-- exist even when a variable is absent from the window being queried, which
-- keeps the shape stable for the parity test.
SELECT
    "FechaHora"                                          AS datetime,
    MAX(CASE WHEN "CodigoVariable" = 'PB_Nal' THEN "Valor" END) AS pb_nal,
    MAX(CASE WHEN "CodigoVariable" = 'PB_Int' THEN "Valor" END) AS pb_int,
    MAX(CASE WHEN "CodigoVariable" = 'PB_Tie' THEN "Valor" END) AS pb_tie
FROM most_mature
GROUP BY "FechaHora"
ORDER BY "FechaHora";
