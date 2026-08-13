-- Average national spot price by hour of day and month, as a wide matrix.
--
-- The shape a forecaster needs before choosing features: Colombia's price has a
-- daily demand cycle (evening peak) on top of a hydrological annual cycle (dry
-- season pushes thermal generation into the marginal slot). Reading both at once
-- shows whether the daily profile changes shape across the year or only shifts
-- level.
--
-- Uses PIVOT, which is safe here because the 12 months are always present in a
-- full-history run.
--
-- Expects a `canonical` view (the output of 01_canonical.sql).

PIVOT (
    SELECT
        EXTRACT(hour  FROM datetime) AS hour_of_day,
        EXTRACT(month FROM datetime) AS month,
        pb_nal
    FROM canonical
    WHERE pb_nal IS NOT NULL
)
ON month
USING ROUND(AVG(pb_nal), 1)
GROUP BY hour_of_day
ORDER BY hour_of_day;
