-- How much does a published price still move after its first publication?
--
-- This is the query behind the claim in the README that settlement revisions
-- are large enough to matter: forecasting or backtesting against the *current*
-- table silently uses numbers that were not knowable at the time.
--
-- For every (variable, hour) it compares the value of the earliest settlement
-- version against the value of the most mature one, then aggregates by year.
--
-- Expects a `raw` view over data/raw/ec6945_*.parquet.

WITH version_rank(version, maturity) AS (
    VALUES ('TX1', 1), ('TX2', 2), ('TXR', 3), ('TXF', 4),
           ('TX3', 5), ('TXA', 5),
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

-- One row per (variable, hour) carrying both ends of the revision chain.
-- The frame must be UNBOUNDED on both sides: with the default frame,
-- LAST_VALUE would return the current row instead of the final one.
bounds AS (
    SELECT DISTINCT
        "CodigoVariable",
        "FechaHora",
        FIRST_VALUE("Valor") OVER w AS first_published,
        LAST_VALUE("Valor")  OVER w AS final_value,
        COUNT(*)             OVER (PARTITION BY "CodigoVariable", "FechaHora") AS versions
    FROM ranked
    WINDOW w AS (
        PARTITION BY "CodigoVariable", "FechaHora"
        ORDER BY maturity
        ROWS BETWEEN UNBOUNDED PRECEDING AND UNBOUNDED FOLLOWING
    )
)

SELECT
    "CodigoVariable"                          AS variable,
    YEAR("FechaHora")                         AS year,
    COUNT(*)                                  AS hours,
    ROUND(AVG(versions), 2)                   AS avg_versions_per_hour,
    ROUND(AVG(ABS(final_value - first_published)), 3) AS avg_abs_revision_cop,
    ROUND(100.0 * AVG(CASE WHEN ABS(final_value - first_published) > 1 THEN 1 ELSE 0 END), 1)
                                              AS pct_revised_over_1_cop,
    ROUND(100.0 * AVG(
        CASE WHEN first_published <> 0
                  AND ABS(final_value - first_published) / ABS(first_published) > 0.05
             THEN 1 ELSE 0 END), 1)           AS pct_revised_over_5_pct
FROM bounds
GROUP BY variable, year
ORDER BY variable, year;
