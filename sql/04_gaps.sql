-- Continuity check: is any hour missing between the first and last observation?
--
-- The README claims the canonical table is continuous with no gaps. This is the
-- query that proves it, and it is meant to return ZERO ROWS. A gap here is a
-- real defect: a forecasting model fed a silently discontinuous series will
-- learn a lag structure that does not exist.
--
-- Anti-join against a generated calendar rather than comparing counts, so the
-- output names the missing hours instead of only signalling that some are gone.
--
-- Timestamps are naive America/Bogota, which has no DST, so a plain hourly
-- series is the correct expectation.
--
-- Expects a `canonical` view (the output of 01_canonical.sql).

WITH span AS (
    SELECT MIN(datetime) AS first_hour, MAX(datetime) AS last_hour
    FROM canonical
),

expected AS (
    SELECT UNNEST(generate_series(first_hour, last_hour, INTERVAL 1 HOUR)) AS datetime
    FROM span
)

SELECT
    e.datetime AS missing_hour,
    EXTRACT(year FROM e.datetime) AS year
FROM expected AS e
LEFT JOIN canonical AS c ON c.datetime = e.datetime
WHERE c.datetime IS NULL
ORDER BY e.datetime;
