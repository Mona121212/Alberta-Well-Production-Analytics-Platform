# check_fact_partitioning.py
# Verifies the month partitioning of gold_fact_production (DEC-023). Read only: no table is written.
# Paste into a Fabric notebook attached to the alberta_wells Lakehouse.
#
# MODE = "before": run BEFORE 02_silver_transform and 03_gold_transform. Records the Gold versions and the
#                  file count of the current (unpartitioned) fact version.
# MODE = "after":  run AFTER both notebooks. Checks the partition layout, the DQ rule tests and reconciliation
#                  tests of the new run, compares old and new Gold versions in both directions with EXCEPT ALL
#                  (run ID columns excluded), and compares one single-month query on the old and new fact version.
#
# Set OLD_VERSIONS to the versions recorded in "before". OUTPUT_PATH (optional) saves the result as JSON.

import json
import statistics
import time

from pyspark.sql import functions as F

MODE = "after"
OLD_VERSIONS = {"gold_fact_production": 7, "gold_dim_well": 7, "gold_dim_date": 3, "gold_dim_operator": 3}
TEST_MONTH = "2025-12-01"
TIMING_ROUNDS = 5
OUTPUT_PATH = None

GOLD_TABLES = list(OLD_VERSIONS)
EXCLUDE = {"gold_fact_production": ["_gold_run_id"], "gold_dim_well": ["_silver_run_id"]}


def latest_version(table):
    return spark.sql(f"DESCRIBE HISTORY {table} LIMIT 1").collect()[0]["version"]


def version_metrics(table, version):
    row = spark.sql(f"DESCRIBE HISTORY {table}").filter(F.col("version") == version).collect()[0]
    return {k: row["operationMetrics"].get(k) for k in ["numFiles", "numOutputRows", "numOutputBytes"]}


def at_version(table, version=None):
    return spark.sql(f"SELECT * FROM {table}" + (f" VERSION AS OF {version}" if version is not None else ""))


def scan_metrics(df):
    # Metrics of the file scan nodes in the executed plan (number of files read, size of files read, ...)
    to_java = spark._jvm.scala.collection.JavaConverters.mapAsJavaMap
    found = []

    def walk(plan):
        cls = plan.getClass().getSimpleName()
        if cls == "AdaptiveSparkPlanExec":
            return walk(plan.executedPlan())
        if cls.endswith("QueryStageExec"):
            return walk(plan.plan())
        if "Scan" in plan.nodeName():
            m = to_java(plan.metrics())
            found.append({k: int(m[k].value()) for k in m.keySet()})
        children = plan.children()
        for i in range(children.length()):
            walk(children.apply(i))

    walk(df._jdf.queryExecution().executedPlan())
    return found


def month_query(version=None):
    return (at_version("gold_fact_production", version)
            .filter(F.col("production_month") == F.lit(TEST_MONTH).cast("date"))
            .groupBy("ProductID").agg(F.sum("volume").alias("volume")))


out = {"mode": MODE,
       "conf": {k: spark.conf.get(k, None) for k in ["spark.sql.parquet.vorder.default",
                                                     "spark.sql.parquet.vorder.enabled",
                                                     "spark.microsoft.delta.optimizeWrite.enabled",
                                                     "spark.databricks.delta.optimizeWrite.enabled"]}}

if MODE == "before":
    out["gold_versions"] = {t: latest_version(t) for t in GOLD_TABLES}
    v = out["gold_versions"]["gold_fact_production"]
    out["fact_version_metrics"] = version_metrics("gold_fact_production", v)
    out["fact_detail"] = spark.sql("DESCRIBE DETAIL gold_fact_production") \
        .select("partitionColumns", "numFiles", "sizeInBytes").collect()[0].asDict()

if MODE == "after":
    # Layout
    out["gold_versions"] = {t: latest_version(t) for t in GOLD_TABLES}
    out["fact_detail"] = spark.sql("DESCRIBE DETAIL gold_fact_production") \
        .select("partitionColumns", "numFiles", "sizeInBytes").collect()[0].asDict()
    out["old_fact_version_metrics"] = version_metrics("gold_fact_production", OLD_VERSIONS["gold_fact_production"])
    out["files_per_month"] = [r.asDict() for r in spark.table("gold_fact_production")
                              .withColumn("_file", F.input_file_name())
                              .groupBy(F.col("production_month").cast("string").alias("month"))
                              .agg(F.countDistinct("_file").alias("files"), F.count("*").alias("rows"))
                              .orderBy("month").collect()]

    # Tests of the new run
    run_id = [r[0] for r in spark.table("silver_petrinex_vol_flagged").select("_dq_run_id").distinct().collect()]
    out["run_id"] = run_id
    out["run_in_dq_run_log"] = spark.table("dq_run_log").filter(F.col("run_id").isin(run_id)).count()
    out["dq_rule_tests"] = [r.asDict() for r in spark.table("dq_rule_test_results").collect()]
    out["reconciliation"] = [r.asDict() for r in spark.table("reconciliation_results")
                             .filter(F.col("run_id").isin(run_id)).collect()]

    # Old vs new, both directions
    out["except_all"] = []
    for t, v in OLD_VERSIONS.items():
        new, old = at_version(t), at_version(t, v)
        assert sorted(old.columns) == sorted(new.columns), (t, old.columns, new.columns)
        cols = [c for c in new.columns if c not in EXCLUDE.get(t, [])]
        o, n = old.select(cols), new.select(cols)
        out["except_all"].append({"table": t, "old_version": v, "new_version": out["gold_versions"][t],
                                  "columns": len(cols), "old_minus_new": o.exceptAll(n).count(),
                                  "new_minus_old": n.exceptAll(o).count()})

    # Single-month query: old (unpartitioned) vs new (partitioned) fact version
    old_v = OLD_VERSIONS["gold_fact_production"]
    runs = {"old": [], "new": []}
    results, metrics, plans = {}, {}, {}
    for label, v in [("old", old_v), ("new", None)]:
        month_query(v).collect()                       # warm-up
        plans[label] = month_query(v)._jdf.queryExecution().executedPlan().toString()
    for _ in range(TIMING_ROUNDS):
        for label, v in [("old", old_v), ("new", None)]:
            spark.catalog.clearCache()
            df = month_query(v)
            start = time.perf_counter()
            rows = df.collect()
            runs[label].append(round(time.perf_counter() - start, 3))
            results[label] = sorted((r["ProductID"], str(r["volume"])) for r in rows)
            metrics[label] = scan_metrics(df)
    out["month_query"] = {
        "month": TEST_MONTH,
        "results_equal": results["old"] == results["new"],
        "seconds": runs,
        "median_seconds": {k: statistics.median(v) for k, v in runs.items()},
        "scan_metrics": metrics,
        "partition_filters_in_plan": {k: "PartitionFilters: [" in p and "PartitionFilters: []" not in p
                                      for k, p in plans.items()},
    }
    for label, v in [("old", old_v), ("new", None)]:
        print(f"--- {label} plan")
        month_query(v).explain()

print(json.dumps(out, indent=1, default=str))
if OUTPUT_PATH:
    notebookutils.fs.put(OUTPUT_PATH, json.dumps(out, default=str), True)
