-- compare_gold_versions.sql
-- Verifies that the Gold split (DEC-021) leaves the Gold tables unchanged.
-- Run in a Fabric notebook SQL cell (Spark SQL) attached to the alberta_wells Lakehouse.
--
-- Part A: run BEFORE 03_gold_transform. Record the latest version of each Gold table.
-- Part B: run AFTER 03_gold_transform. Compare the recorded (old) version with the current (new) table
--         in both directions with EXCEPT ALL. Every diff_rows value must be 0.
--
-- Run ID columns are excluded because they change on every run:
-- - gold_fact_production: _gold_run_id
-- - gold_dim_well: _silver_run_id (copied from silver_dim_well, which 02_silver_transform rewrites on every run)
-- Spark 3.5 has no SELECT * EXCEPT, so the columns of these two tables are listed explicitly.


-- =====================================================================================================
-- Part A: before the run. Note the "version" value of each result.
-- =====================================================================================================

DESCRIBE HISTORY gold_fact_production LIMIT 1;
DESCRIBE HISTORY gold_dim_well LIMIT 1;
DESCRIBE HISTORY gold_dim_date LIMIT 1;
DESCRIBE HISTORY gold_dim_operator LIMIT 1;


-- =====================================================================================================
-- Part B: after the run. Versions recorded in Part A: fact=5, dim_well=5, dim_date=1, dim_operator=1.
-- =====================================================================================================

WITH
fact_old AS (
    SELECT production_month, well_key, ProductID, ReportingFacilityID, OperatorBAID, OperatorName,
           volume, energy, hours, submission_date, _source_file
    FROM gold_fact_production VERSION AS OF 5
),
fact_new AS (
    SELECT production_month, well_key, ProductID, ReportingFacilityID, OperatorBAID, OperatorName,
           volume, energy, hours, submission_date, _source_file
    FROM gold_fact_production
),
dim_well_old AS (
    SELECT well_key, well_uwi, licence_number, well_name, status_fluid, status_mode, status_type,
           licence_status, in_production_string, in_bh, is_placeholder
    FROM gold_dim_well VERSION AS OF 5
),
dim_well_new AS (
    SELECT well_key, well_uwi, licence_number, well_name, status_fluid, status_mode, status_type,
           licence_status, in_production_string, in_bh, is_placeholder
    FROM gold_dim_well
)
SELECT 'gold_fact_production' AS table_name, 'old_minus_new' AS direction, COUNT(*) AS diff_rows
FROM (SELECT * FROM fact_old EXCEPT ALL SELECT * FROM fact_new)
UNION ALL
SELECT 'gold_fact_production', 'new_minus_old', COUNT(*)
FROM (SELECT * FROM fact_new EXCEPT ALL SELECT * FROM fact_old)
UNION ALL
SELECT 'gold_dim_well', 'old_minus_new', COUNT(*)
FROM (SELECT * FROM dim_well_old EXCEPT ALL SELECT * FROM dim_well_new)
UNION ALL
SELECT 'gold_dim_well', 'new_minus_old', COUNT(*)
FROM (SELECT * FROM dim_well_new EXCEPT ALL SELECT * FROM dim_well_old)
UNION ALL
SELECT 'gold_dim_date', 'old_minus_new', COUNT(*)
FROM (SELECT * FROM gold_dim_date VERSION AS OF 1 EXCEPT ALL SELECT * FROM gold_dim_date)
UNION ALL
SELECT 'gold_dim_date', 'new_minus_old', COUNT(*)
FROM (SELECT * FROM gold_dim_date EXCEPT ALL SELECT * FROM gold_dim_date VERSION AS OF 1)
UNION ALL
SELECT 'gold_dim_operator', 'old_minus_new', COUNT(*)
FROM (SELECT * FROM gold_dim_operator VERSION AS OF 1 EXCEPT ALL SELECT * FROM gold_dim_operator)
UNION ALL
SELECT 'gold_dim_operator', 'new_minus_old', COUNT(*)
FROM (SELECT * FROM gold_dim_operator EXCEPT ALL SELECT * FROM gold_dim_operator VERSION AS OF 1)
ORDER BY table_name, direction;
