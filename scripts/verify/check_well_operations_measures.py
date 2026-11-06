# check_well_operations_measures.py
# Computes the Well Operations and Sensor Health measures with Spark SQL, independently of DAX (DEC-035). Read only on
# the tables: the only thing written is the JSON file at OUTPUT_PATH, which the DAX comparison reads.
# Paste into a Fabric notebook attached to the alberta_wells Lakehouse, with no Spark job running.
#
# The health numbers come from silver_sensor_readings, not gold_sensor_hourly, so a wrong aggregation in Gold shows up.
# "Latest run" is the sensor run with the latest started_at in pipeline_run_log: run IDs are random, so MAX(run_id) means
# nothing. sensor_reconciliation_results keeps every run; the Gold tables hold only the latest.

import json

OUTPUT_PATH = "/lakehouse/default/Files/tmp_verify_well_operations.json"
HISTORY = "stream_run_id IS NULL"


def rows(query):
    return [r.asDict() for r in spark.sql(query).collect()]


latest = rows("""SELECT run_id FROM pipeline_run_log WHERE source = 'sensor' AND status = 'SUCCESS'
                 ORDER BY started_at DESC LIMIT 1""")[0]["run_id"]
gold_runs = {t: [r["_run_id"] for r in rows(f"SELECT DISTINCT _run_id FROM {t}")]
             for t in ["gold_sensor_hourly", "gold_downtime_events", "gold_sensor_reconciliation", "gold_sensor_tags",
                       "silver_sensor_readings"]}
latest_tests = rows(f"SELECT test_name, expected, actual, status FROM sensor_reconciliation_results WHERE run_id = '{latest}'")

HEALTH = f"""SELECT {{keys}}
    COUNT(*) AS readings,
    SUM(CASE WHEN quality = 'Good' THEN 1 ELSE 0 END) AS good,
    SUM(CASE WHEN quality_reason = 'frozen' THEN 1 ELSE 0 END) AS frozen,
    SUM(CASE WHEN quality_reason = 'outside_physical_range' THEN 1 ELSE 0 END) AS out_of_range,
    SUM(CASE WHEN quality_reason = 'missing' THEN 1 ELSE 0 END) AS missing
    FROM silver_sensor_readings WHERE {HISTORY} {{group}}"""
DOWNTIME = """SELECT {keys}
    COUNT(*) AS events, SUM(duration_hours) AS hours,
    SUM(CASE WHEN product = 'OIL' THEN lost_volume ELSE 0 END) AS lost_oil,
    SUM(CASE WHEN product = 'GAS' THEN lost_volume ELSE 0 END) AS lost_gas
    FROM gold_downtime_events {group}"""
RECON = """SELECT {keys}
    SUM(CASE WHEN product = 'OIL' THEN sensor_volume END) AS sensor_oil,
    SUM(CASE WHEN product = 'GAS' THEN sensor_volume END) AS sensor_gas,
    SUM(sensor_volume) AS sensor_volume, SUM(reported_volume) AS reported_volume,
    SUM(CASE WHEN needs_investigation THEN 1 ELSE 0 END) AS to_investigate,
    MAX(flow_good_ratio_pct) AS flow_good_ratio_pct, MAX(likely_cause) AS likely_cause, MAX(product) AS product
    FROM gold_sensor_reconciliation {group}"""

result = {
    "latest_run": latest, "gold_runs": gold_runs, "latest_tests": latest_tests,
    "total": {"health": rows(HEALTH.format(keys="", group=""))[0], "downtime": rows(DOWNTIME.format(keys="", group=""))[0],
              "recon": rows(RECON.format(keys="", group=""))[0]},
    "well": {"health": rows(HEALTH.format(keys="well_key,", group="GROUP BY well_key")),
             "downtime": rows(DOWNTIME.format(keys="well_key,", group="GROUP BY well_key")),
             "recon": rows(RECON.format(keys="well_key,", group="GROUP BY well_key"))},
    "tag": rows(HEALTH.format(keys="well_key, tag_code,", group="GROUP BY well_key, tag_code")),
    "events": [{k: str(v) if k.endswith("_local") else v for k, v in r.items()} for r in rows(
        """SELECT well_key, start_local, end_local, duration_hours, lost_volume, product FROM gold_downtime_events""")],
    "top10": rows("""SELECT well_key, SUM(duration_hours) AS hours FROM gold_downtime_events
                     GROUP BY well_key ORDER BY hours DESC, well_key LIMIT 11"""),
    "markers": rows("""SELECT COUNT(*) AS non_good, SUM(CASE WHEN flagged_value IS NULL THEN 1 ELSE 0 END) AS no_marker,
                       SUM(CASE WHEN flagged_value = -9999 THEN 1 ELSE 0 END) AS at_minus_9999
                       FROM gold_sensor_hourly WHERE source = 'backfill' AND flagged_reason IS NOT NULL""")[0],
}
with open(OUTPUT_PATH, "w") as fh:
    json.dump(result, fh, default=float)
print("latest run:", latest, "| gold runs:", gold_runs)
print("tests:", latest_tests)
print("total:", result["total"])
