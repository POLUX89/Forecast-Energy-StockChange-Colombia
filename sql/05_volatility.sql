-- Monthly level, dispersion and tails of the national spot price, with a
-- 12-month rolling mean for context.
--
-- Percentiles rather than mean ± sd alone: the price distribution is strongly
-- right-skewed (scarcity hours can be an order of magnitude above the median),
-- so the mean overstates a typical hour and the standard deviation understates
-- the tail. p90/p50 is the ratio worth watching before choosing a loss function
-- or deciding whether to model the log price.
--
-- Expects a `canonical` view (the output of 01_canonical.sql).

WITH monthly AS (
    SELECT
        DATE_TRUNC('month', datetime)                    AS month,
        COUNT(*)                                         AS hours,
        ROUND(AVG(pb_nal), 1)                            AS mean_cop,
        ROUND(STDDEV_SAMP(pb_nal), 1)                    AS sd_cop,
        ROUND(QUANTILE_CONT(pb_nal, 0.10), 1)            AS p10,
        ROUND(QUANTILE_CONT(pb_nal, 0.50), 1)            AS p50,
        ROUND(QUANTILE_CONT(pb_nal, 0.90), 1)            AS p90
    FROM canonical
    WHERE pb_nal IS NOT NULL
    GROUP BY month
)

SELECT
    month,
    hours,
    mean_cop,
    sd_cop,
    p10,
    p50,
    p90,
    ROUND(p90 / NULLIF(p50, 0), 2) AS tail_ratio,
    -- Trailing 12-month mean: separates a genuinely expensive month from one
    -- that only looks expensive against its immediate neighbours.
    ROUND(AVG(mean_cop) OVER (
        ORDER BY month ROWS BETWEEN 11 PRECEDING AND CURRENT ROW
    ), 1) AS rolling_12m_mean
FROM monthly
ORDER BY month;
