#!/usr/bin/env python
# coding: utf-8

# ## 02_silver_transform
# 
# null

# In[1]:


# Welcome to your new notebook
# Type here in the cell editor to add code!
import uuid
from datetime import datetime
from pyspark.sql import functions as F

RUN_ID = str(uuid.uuid4())[:8]
META_COLS = ["_source_file", "_ingest_time", "_run_id"]

def normalize_strings(sdf, exclude=()):
    """Trim every string column and convert empty strings to NULL."""
    for c, t in sdf.dtypes:
        if t == "string" and c not in exclude:
            trimmed = F.trim(F.col(c))
            sdf = sdf.withColumn(c, F.when(trimmed == "", F.lit(None)).otherwise(trimmed))
    return sdf

print("RUN_ID:", RUN_ID)



bronze = spark.table("bronze_petrinex_vol")
s = normalize_strings(bronze, exclude=META_COLS)

s = (s
    .withColumn("production_month", F.to_date(F.concat(F.col("ProductionMonth"), F.lit("-01"))))
    .withColumn("submission_date", F.to_date(F.col("SubmissionDate")))
    .withColumn("volume", F.expr("try_cast(Volume as decimal(18,3))"))
    .withColumn("energy", F.expr("try_cast(Energy as decimal(18,3))"))
    .withColumn("hours", F.expr("try_cast(Hours as int)"))
    .withColumn("well_key", F.when(F.col("FromToIDType") == "WI",
                                   F.upper(F.col("FromToIDIdentifier"))))
    .withColumn("_silver_run_id", F.lit(RUN_ID))
)

# Cast check: raw value present but typed value is NULL
def cast_failed(raw, typed):
    return F.sum(F.when(F.col(raw).isNotNull() & F.col(typed).isNull(), 1).otherwise(0))

display(s.agg(
    F.count("*").alias("rows"),
    cast_failed("Volume", "volume").alias("volume_cast_failed"),
    cast_failed("Energy", "energy").alias("energy_cast_failed"),
    cast_failed("Hours", "hours").alias("hours_cast_failed"),
    F.sum(F.when(F.col("CCICode").isNull(), 1).otherwise(0)).alias("cci_null_after_trim"),
    F.sum(F.when(F.col("well_key").isNotNull(), 1).otherwise(0)).alias("rows_with_well_key"),
))



files = [r[0] for r in bronze.select("_source_file").distinct().collect()]

if spark.catalog.tableExists("silver_petrinex_vol"):
    for f in files:
        spark.sql(f"DELETE FROM silver_petrinex_vol WHERE _source_file = '{f}'")

s.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable("silver_petrinex_vol")

print("silver rows:", spark.table("silver_petrinex_vol").count(), "bronze rows:", bronze.count())
print("new columns present:", [c for c in ["volume_raw", "volume_masked", "hours_masked", "cast_failed_unexpected"]
                               if c in spark.table("silver_petrinex_vol").columns])


UWI_PATTERN = r"^(\w{2})/(\d{2})-(\d{2})-(\d{3})-(\d{2})W(\d)/(\d+)$"

def add_well_key(sdf, uwi_col="Well_UWI"):
    u = F.upper(F.col(uwi_col))
    p = [F.regexp_extract(u, UWI_PATTERN, i) for i in range(1, 8)]
    key = F.concat(F.lit("1"), p[0], p[1], p[2], p[3], p[4], F.lit("W"), p[5], F.lpad(p[6], 2, "0"))
    return sdf.withColumn("well_key", F.when(u.rlike(UWI_PATTERN), key))


ps = add_well_key(normalize_strings(spark.table("bronze_st37_production_string"), exclude=META_COLS))
bh = add_well_key(normalize_strings(spark.table("bronze_st37_bh"), exclude=META_COLS))

print("production_string rows:", ps.count(), " null well_key:", ps.filter(F.col("well_key").isNull()).count())
print("bh rows:", bh.count(), " null well_key:", bh.filter(F.col("well_key").isNull()).count())

ps_sel = (ps.filter(F.col("well_key").isNotNull())
    .select("well_key",
            F.col("Well_UWI").alias("well_uwi"),
            F.col("Well_Licence_Number").alias("licence_number"),
            F.col("Well_Name").alias("well_name"),
            F.col("Status_Fluid").alias("status_fluid"),
            F.col("Status_Mode").alias("status_mode"),
            F.col("Status_Type").alias("status_type"))
    .withColumn("in_production_string", F.lit(True)))

bh_sel = (bh.filter(F.col("well_key").isNotNull())
    .select("well_key",
            F.col("Well_UWI").alias("bh_uwi"),
            F.col("Well_Licence_Number").alias("bh_licence_number"),
            F.col("Well_Name").alias("bh_well_name"),
            F.col("Licence_Status").alias("licence_status"))
    .withColumn("in_bh", F.lit(True)))

dim = (ps_sel.join(bh_sel, "well_key", "full_outer")
    .select("well_key",
            F.coalesce("well_uwi", "bh_uwi").alias("well_uwi"),
            F.coalesce("licence_number", "bh_licence_number").alias("licence_number"),
            F.coalesce("well_name", "bh_well_name").alias("well_name"),
            "status_fluid", "status_mode", "status_type", "licence_status",
            F.coalesce("in_production_string", F.lit(False)).alias("in_production_string"),
            F.coalesce("in_bh", F.lit(False)).alias("in_bh"),
            F.lit(False).alias("is_placeholder"),
            F.lit(RUN_ID).alias("_silver_run_id")))

dim.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("silver_dim_well")

d = spark.table("silver_dim_well")
print("dim rows:", d.count(), " distinct well_key:", d.select("well_key").distinct().count())
display(d.groupBy("in_production_string", "in_bh").count())




# In[2]:


sp = spark.table("silver_petrinex_vol")
dim = spark.table("silver_dim_well")

activity_rows = sp.filter(F.col("ActivityID").isNotNull())
prod_wi = sp.filter((F.col("ActivityID") == "PROD") & (F.col("FromToIDType") == "WI"))
max_hours = F.dayofmonth(F.last_day("production_month")) * 24

key_cols = ["production_month", "ReportingFacilityID", "ActivityID", "ProductID", "FromToID"]
dup_groups = activity_rows.groupBy(*key_cols).count().filter(F.col("count") > 1)
dup_extra = dup_groups.agg(F.sum(F.col("count") - 1)).collect()[0][0] or 0

missing_dim = prod_wi.join(dim.select("well_key"), "well_key", "left_anti")

results = {
    "duplicate_key_groups": dup_groups.count(),
    "duplicate_extra_rows": dup_extra,
    "prod_missing_volume": sp.filter((F.col("ActivityID") == "PROD") & F.col("volume").isNull()).count(),
    "negative_volume": sp.filter(F.col("volume") < 0).count(),
    "hours_above_month_max": sp.filter(F.col("hours") > max_hours).count(),
    "wi_row_missing_well_key": sp.filter((F.col("FromToIDType") == "WI") & F.col("well_key").isNull()).count(),
    "prod_rows_well_not_in_dim": missing_dim.count(),
    "prod_distinct_wells_not_in_dim": missing_dim.select("well_key").distinct().count(),
}

for k, v in results.items():
    print(f"{k}: {v}")


# In[3]:


# Negative volumes by activity and product
display(sp.filter(F.col("volume") < 0)
    .groupBy("ActivityID", "ProductID")
    .agg(F.count("*").alias("rows"), F.sum("volume").alias("total_volume"))
    .orderBy(F.desc("rows")).limit(15))

# Negative volumes inside PROD rows only
print("negative PROD rows:", sp.filter((F.col("ActivityID") == "PROD") & (F.col("volume") < 0)).count())

# The 4 duplicate groups in detail
dups = activity_rows.groupBy(*key_cols).count().filter(F.col("count") > 1).drop("count")
display(activity_rows.join(dups, key_cols, "inner")
    .select(*key_cols, "volume", "energy", "hours", "submission_date", "OperatorName")
    .orderBy(*key_cols))


# In[6]:


needed = ["volume_raw", "volume_masked", "hours_masked", "energy_masked", "cast_failed_unexpected"]
have = spark.table("silver_petrinex_vol").columns
print("missing columns:", [c for c in needed if c not in have])


# In[7]:


new_cols = ["volume_raw", "volume_masked", "hours_masked", "energy_masked", "cast_failed_unexpected"]
missing_in_s = [c for c in new_cols if c not in s.columns]
if missing_in_s:
    raise ValueError(f"DataFrame s is missing {missing_in_s}. Re-run the Silver cleaning cell first.")

files = [r[0] for r in bronze.select("_source_file").distinct().collect()]
print("files to load:", files)

if spark.catalog.tableExists("silver_petrinex_vol"):
    for f in files:
        spark.sql(f"DELETE FROM silver_petrinex_vol WHERE _source_file = '{f}'")

s.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable("silver_petrinex_vol")

silver = spark.table("silver_petrinex_vol")
print("silver rows:", silver.count(), " bronze rows:", bronze.count())
print("missing columns:", [c for c in new_cols if c not in silver.columns])


# In[8]:


import uuid
from pyspark.sql import functions as F

META_COLS = ["_source_file", "_ingest_time", "_run_id"]
SILVER_RUN_ID = str(uuid.uuid4())[:8]

def normalize_strings(sdf, exclude=()):
    for c, t in sdf.dtypes:
        if t == "string" and c not in exclude:
            trimmed = F.trim(F.col(c))
            sdf = sdf.withColumn(c, F.when(trimmed == "", F.lit(None)).otherwise(trimmed))
    return sdf

def is_masked(raw_col):
    return F.coalesce(F.col(raw_col) == "***", F.lit(False))

def unexpected_failure(raw_col, typed_col):
    return F.col(raw_col).isNotNull() & F.col(typed_col).isNull() & ~is_masked(raw_col)

bronze = spark.table("bronze_petrinex_vol")
s = normalize_strings(bronze, exclude=META_COLS)

s = (s
    .withColumn("volume_raw", F.col("Volume"))
    .withColumn("energy_raw", F.col("Energy"))
    .withColumn("hours_raw", F.col("Hours"))
    .withColumn("production_month", F.to_date(F.concat(F.col("ProductionMonth"), F.lit("-01"))))
    .withColumn("submission_date", F.to_date(F.col("SubmissionDate")))
    .withColumn("volume", F.expr("try_cast(volume_raw as decimal(18,3))"))
    .withColumn("energy", F.expr("try_cast(energy_raw as decimal(18,3))"))
    .withColumn("hours", F.expr("try_cast(hours_raw as int)"))
    .withColumn("well_key", F.when(F.col("FromToIDType") == "WI", F.upper(F.col("FromToIDIdentifier"))))
    .withColumn("volume_masked", is_masked("volume_raw"))
    .withColumn("energy_masked", is_masked("energy_raw"))
    .withColumn("hours_masked", is_masked("hours_raw"))
    .withColumn("cast_failed_unexpected",
                unexpected_failure("volume_raw", "volume")
                | unexpected_failure("energy_raw", "energy")
                | unexpected_failure("hours_raw", "hours"))
    .withColumn("_silver_run_id", F.lit(SILVER_RUN_ID)))

new_cols = ["volume_raw", "energy_raw", "hours_raw", "volume_masked",
            "energy_masked", "hours_masked", "cast_failed_unexpected"]
print("new columns in s:", [c for c in new_cols if c in s.columns])

# Reload safely: remove the rows of these source files, then append the new version
files = [r[0] for r in bronze.select("_source_file").distinct().collect()]
print("files to load:", files)

if spark.catalog.tableExists("silver_petrinex_vol"):
    for f in files:
        spark.sql(f"DELETE FROM silver_petrinex_vol WHERE _source_file = '{f}'")

s.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable("silver_petrinex_vol")

silver = spark.table("silver_petrinex_vol")
print("silver rows:", silver.count(), " bronze rows:", bronze.count())
print("missing columns:", [c for c in new_cols if c not in silver.columns])
print("masked volume:", silver.filter("volume_masked").count(),
      " masked hours:", silver.filter("hours_masked").count(),
      " unexpected failures:", silver.filter("cast_failed_unexpected").count())


# In[9]:


from pyspark.sql.window import Window

sp = spark.table("silver_petrinex_vol")
dim = spark.table("silver_dim_well")

required = ["volume_masked", "hours_masked", "cast_failed_unexpected"]
absent = [c for c in required if c not in sp.columns]
if absent:
    raise ValueError(f"silver_petrinex_vol is missing {absent}. Run the Silver write cell first.")

key_cols = ["production_month", "ReportingFacilityID", "ActivityID", "ProductID", "FromToID"]
business_cols = [c for c in sp.columns if c not in META_COLS + ["_silver_run_id"]]
max_hours = F.dayofmonth(F.last_day("production_month")) * 24

base = (sp
    .withColumn("_rn", F.row_number().over(Window.partitionBy(*business_cols).orderBy(F.lit(1))))
    .withColumn("_key_count", F.count("*").over(Window.partitionBy(*key_cols)))
    .join(dim.select("well_key", F.lit(True).alias("_in_dim")), "well_key", "left"))

is_prod = F.col("ActivityID") == "PROD"
rules = [
    ("exact_duplicate", "reject", F.col("_rn") > 1),
    ("prod_negative_volume", "reject", is_prod & (F.col("volume") < 0)),
    ("prod_missing_volume", "reject", is_prod & F.col("volume").isNull()),
    ("hours_above_month_max", "reject", F.col("hours") > max_hours),
    ("prod_well_not_in_dim", "warning",
        is_prod & (F.col("FromToIDType") == "WI") & F.col("_in_dim").isNull()),
    ("duplicate_key_masked_fromto", "warning",
        (F.col("FromToID") == "***") & (F.col("_key_count") > 1)),
]

rules += [
    ("masked_volume", "info", F.coalesce(F.col("volume_masked"), F.lit(False))),
    ("masked_hours", "info", F.coalesce(F.col("hours_masked"), F.lit(False))),
    ("unexpected_cast_failure", "warning", F.col("cast_failed_unexpected")),
]

out_cols = key_cols + ["volume", "_source_file"]
dq_rows, rejected = [], None
for name, sev, cond in rules:
    flagged = base.filter(cond)
    dq_rows.append((RUN_ID, "silver", "silver_petrinex_vol", name, sev, flagged.count()))
    if sev == "reject":
        part = (flagged.select(*out_cols)
                .withColumn("rule_name", F.lit(name))
                .withColumn("run_id", F.lit(RUN_ID)))
        rejected = part if rejected is None else rejected.unionByName(part)

spark.createDataFrame(dq_rows, "run_id string, layer string, table_name string, rule_name string, severity string, rows_flagged long") \
    .write.format("delta").mode("append").saveAsTable("dq_results")
rejected.write.format("delta").mode("append").saveAsTable("rejected_records")

print(len(rules), "rules")
display(spark.table("dq_results").filter(F.col("run_id") == RUN_ID))
print("rejected rows:", spark.table("rejected_records").filter(F.col("run_id") == RUN_ID).count())


# In[10]:


from functools import reduce

# Flag each silver row: rejected or not, in scope for the fact table or not
reject_cond = reduce(lambda a, b: a | b,
    [F.coalesce(cond, F.lit(False)) for _, sev, cond in rules if sev == "reject"])
in_scope = F.coalesce(is_prod & (F.col("FromToIDType") == "WI"), F.lit(False))

flagged = (base
    .withColumn("_rejected", reject_cond)
    .withColumn("_in_scope", in_scope))

fact_rows = flagged.filter(~F.col("_rejected") & F.col("_in_scope"))

# 1) Gold dim: silver dim plus placeholder rows for wells missing from ST37
missing = (fact_rows.select("well_key").distinct()
    .join(dim.select("well_key"), "well_key", "left_anti"))

placeholders = missing
for c, t in dim.dtypes:
    if c != "well_key":
        placeholders = placeholders.withColumn(c, F.lit(None).cast(t))
placeholders = (placeholders
    .withColumn("in_production_string", F.lit(False))
    .withColumn("in_bh", F.lit(False))
    .withColumn("is_placeholder", F.lit(True))
    .select(dim.columns))

gold_dim = dim.unionByName(placeholders)
gold_dim.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("gold_dim_well")
print("placeholder wells:", placeholders.count())

# 2) Gold fact
fact = (fact_rows
    .select("production_month", "well_key", "ProductID", "ReportingFacilityID",
            "OperatorBAID", "OperatorName", "volume", "energy", "hours",
            "submission_date", "_source_file")
    .withColumn("_gold_run_id", F.lit(RUN_ID)))
fact.write.format("delta").mode("overwrite").option("overwriteSchema", "true").saveAsTable("gold_fact_production")

# 3) Reconciliation tests
g = spark.table("gold_fact_production")
gd = spark.table("gold_dim_well")
n_silver = sp.count()
n_rejected = flagged.filter(F.col("_rejected")).count()
n_out_of_scope = flagged.filter(~F.col("_rejected") & ~F.col("_in_scope")).count()

grain = ["production_month", "ReportingFacilityID", "well_key", "ProductID"]
dup_grain = g.groupBy(*grain).count().filter("count > 1").count()
orphans = g.join(gd.select("well_key"), "well_key", "left_anti").count()

silver_sum = fact_rows.groupBy("ProductID").agg(F.sum("volume").alias("s"))
gold_sum = g.groupBy("ProductID").agg(F.sum("volume").alias("g"))
sum_mismatch = (silver_sum.join(gold_sum, "ProductID", "full_outer")
    .filter(F.abs(F.coalesce("s", F.lit(0)) - F.coalesce("g", F.lit(0))) > 0.001)
    .count())

tests = [
    ("bronze_equals_silver_rows", spark.table("bronze_petrinex_vol").count(), n_silver),
    ("silver_equals_rejected_plus_out_of_scope_plus_gold", n_silver, n_rejected + n_out_of_scope + g.count()),
    ("gold_volume_totals_match_silver_by_product", 0, sum_mismatch),
    ("gold_fact_grain_unique", 0, dup_grain),
    ("gold_fact_no_orphan_wells", 0, orphans),
]
rows = [(RUN_ID, n, int(e), int(a), "PASS" if e == a else "FAIL") for n, e, a in tests]
spark.createDataFrame(rows, "run_id string, test_name string, expected long, actual long, status string") \
    .write.format("delta").mode("append").saveAsTable("reconciliation_results")

display(spark.table("reconciliation_results").filter(F.col("run_id") == RUN_ID))
print("gold fact rows:", g.count(), " gold dim rows:", gd.count())


# In[11]:


from functools import reduce
from pyspark.sql.window import Window

# Fake bad rows to test the reject rules. Nothing is written to Silver, Gold or rejected_records.

# 1) Take one real production row as a template, collected to the driver so every case uses the same row
seed_rows = (sp.filter(is_prod & (F.col("FromToIDType") == "WI")
                       & F.col("volume").isNotNull() & F.col("hours").isNotNull())
               .join(dim.select("well_key"), "well_key", "left_semi")
               .select(sp.columns)
               .limit(1).collect())
seed = spark.createDataFrame(seed_rows, sp.schema)

def make_case(name, **changes):
    d = seed
    for col, val in changes.items():
        d = d.withColumn(col, F.lit(val).cast(dict(seed.dtypes)[col]))
    return d.withColumn("test_case", F.lit(name))

cases = [
    make_case("clean_row", volume=1.234),
    make_case("negative_prod", volume=-100.0),
    make_case("missing_prod_volume", volume=None),
    make_case("hours_too_high", hours=99999),
    make_case("exact_duplicate"),
    make_case("exact_duplicate"),
    make_case("unknown_well", well_key="199999999999W400", volume=2.345),
]
test_df = reduce(lambda a, b: a.unionByName(b), cases)

# 2) Apply the same helper columns and the same rules list used in the real pipeline
tb = (test_df
    .withColumn("_rn", F.row_number().over(Window.partitionBy(*business_cols).orderBy(F.lit(1))))
    .withColumn("_key_count", F.count("*").over(Window.partitionBy(*key_cols)))
    .join(dim.select("well_key", F.lit(True).alias("_in_dim")), "well_key", "left"))

for name, sev, cond in rules:
    tb = tb.withColumn(f"hit_{name}", F.coalesce(cond, F.lit(False)))

# 3) Count how many rows each rule flagged per test case
agg = tb.groupBy("test_case").agg(*[F.sum(F.col(f"hit_{n}").cast("int")).alias(n) for n, _, _ in rules])
actual = {r["test_case"]: {n: r[n] for n, _, _ in rules if r[n]} for r in agg.collect()}

expected = {
    "clean_row": {},
    "negative_prod": {"prod_negative_volume": 1},
    "missing_prod_volume": {"prod_missing_volume": 1},
    "hours_too_high": {"hours_above_month_max": 1},
    "exact_duplicate": {"exact_duplicate": 1},      # 2 identical rows: only the second one is rejected
    "unknown_well": {"prod_well_not_in_dim": 1},    # warning only, row must be kept
}

results = []
for case_name, exp in expected.items():
    act = actual.get(case_name, {})
    results.append((case_name, str(exp), str(act), "PASS" if exp == act else "FAIL"))

# 4) Rows that would reach Gold: clean_row, unknown_well, and one copy of the duplicate = 3
reject_any = reduce(lambda a, b: a | b, [F.col(f"hit_{n}") for n, sev, _ in rules if sev == "reject"])
kept = tb.filter(~reject_any).count()
results.append(("rows_kept_for_gold", "3", str(kept), "PASS" if kept == 3 else "FAIL"))

res_df = spark.createDataFrame(results, "test_case string, expected string, actual string, status string")
res_df.write.format("delta").mode("overwrite").saveAsTable("dq_rule_test_results")
display(res_df)


# In[12]:


from dateutil.relativedelta import relativedelta

sp = spark.table("silver_petrinex_vol")

# Step A: which months are loaded and how many rows each
months_df = sp.groupBy("production_month").count().orderBy("production_month")
display(months_df)
loaded = [r["production_month"] for r in months_df.collect()]

# Step B: calendar gaps between the first and last loaded month
missing = []
if loaded:
    cur = loaded[0]
    while cur < loaded[-1]:
        cur = cur + relativedelta(months=1)
        if cur not in loaded:
            missing.append(cur)
print("missing calendar months:", missing)

# Step C: wells with production that appear or disappear between consecutive months
wells = (sp.filter((F.col("ActivityID") == "PROD") & (F.col("FromToIDType") == "WI"))
           .select("production_month", "well_key").distinct())

dq_rows = [(RUN_ID, "silver", "silver_petrinex_vol", "missing_calendar_month", "warning", len(missing))]

for prev, curr in zip(loaded[:-1], loaded[1:]):
    a = wells.filter(F.col("production_month") == F.lit(prev)).select("well_key")
    b = wells.filter(F.col("production_month") == F.lit(curr)).select("well_key")
    n_prev, n_curr = a.count(), b.count()
    gone = a.join(b, "well_key", "left_anti").count()
    new = b.join(a, "well_key", "left_anti").count()
    print(prev, "->", curr, "| wells:", n_prev, n_curr, "| disappeared:", gone, "| new:", new)
    dq_rows.append((RUN_ID, "silver", "silver_petrinex_vol",
                    f"wells_disappeared_{prev}_to_{curr}", "warning", gone))
    dq_rows.append((RUN_ID, "silver", "silver_petrinex_vol",
                    f"wells_new_{prev}_to_{curr}", "info", new))

spark.createDataFrame(dq_rows, "run_id string, layer string, table_name string, rule_name string, severity string, rows_flagged long") \
    .write.format("delta").mode("append").saveAsTable("dq_results")

display(spark.table("dq_results").filter(F.col("run_id") == RUN_ID))


# In[16]:


r = spark.table("dq_results").filter(F.col("run_id") == RUN_ID)
print("rows for this run:", r.count())
display(r.orderBy("rule_name"))


# In[13]:


# Part 1: raw non-null counts in Bronze vs typed non-null counts in Silver
b = normalize_strings(spark.table("bronze_petrinex_vol"), exclude=META_COLS)
sp = spark.table("silver_petrinex_vol")

def nonnull_counts(df, cols):
    return df.agg(*[F.sum(F.col(c).isNotNull().cast("int")).alias(c) for c in cols]).collect()[0].asDict()

print("bronze raw non-null:", nonnull_counts(b, ["Volume", "Energy", "Hours"]))
print("silver typed non-null:", nonnull_counts(sp, ["volume", "energy", "hours"]))

# Part 2: PROD rows in Bronze whose volume cannot be used, with the raw value
bad = (b.filter(F.col("ActivityID") == "PROD")
        .withColumn("volume_try", F.expr("try_cast(Volume as decimal(18,3))"))
        .filter(F.col("volume_try").isNull()))
print("PROD rows with unusable volume in Bronze:", bad.count())
display(bad.select("ProductionMonth", "ReportingFacilityID", "OperatorName", "ProductID",
                   "FromToID", "Volume", "Energy", "Hours", "SubmissionDate"))

# Part 3: distinct wells and PROD rows per month
display(sp.filter((F.col("ActivityID") == "PROD") & (F.col("FromToIDType") == "WI"))
          .groupBy("production_month")
          .agg(F.countDistinct("well_key").alias("wells"), F.count("*").alias("prod_rows"))
          .orderBy("production_month"))


# In[14]:


def failed_values(raw_col, typed_expr):
    return (b.filter(F.col(raw_col).isNotNull())
             .withColumn("typed", F.expr(typed_expr))
             .filter(F.col("typed").isNull()))

vol_bad = failed_values("Volume", "try_cast(Volume as decimal(18,3))")
hrs_bad = failed_values("Hours", "try_cast(Hours as int)")

print("Volume cast failures:", vol_bad.count())
display(vol_bad.groupBy("Volume", "ActivityID").count().orderBy(F.desc("count")).limit(10))

print("Hours cast failures:", hrs_bad.count())
display(hrs_bad.groupBy("Hours", "ActivityID").count().orderBy(F.desc("count")).limit(10))

# Distinct wells per month straight from Bronze, independent of Silver
display(b.filter((F.col("ActivityID") == "PROD") & (F.col("FromToIDType") == "WI"))
          .groupBy("ProductionMonth")
          .agg(F.countDistinct(F.upper("FromToIDIdentifier")).alias("wells")))


# In[15]:


bronze = spark.table("bronze_petrinex_vol")
s = normalize_strings(bronze, exclude=META_COLS)

# Keep the raw text of the numeric columns before any casting
s = (s
    .withColumn("volume_raw", F.col("Volume"))
    .withColumn("energy_raw", F.col("Energy"))
    .withColumn("hours_raw", F.col("Hours")))

s = (s
    .withColumn("production_month", F.to_date(F.concat(F.col("ProductionMonth"), F.lit("-01"))))
    .withColumn("submission_date", F.to_date(F.col("SubmissionDate")))
    .withColumn("volume", F.expr("try_cast(volume_raw as decimal(18,3))"))
    .withColumn("energy", F.expr("try_cast(energy_raw as decimal(18,3))"))
    .withColumn("hours", F.expr("try_cast(hours_raw as int)"))
    .withColumn("well_key", F.when(F.col("FromToIDType") == "WI",
                                   F.upper(F.col("FromToIDIdentifier"))))
    .withColumn("_silver_run_id", F.lit(RUN_ID))
)

def is_masked(raw_col):
    return F.coalesce(F.col(raw_col) == "***", F.lit(False))

def unexpected_failure(raw_col, typed_col):
    return (F.col(raw_col).isNotNull() & F.col(typed_col).isNull() & ~is_masked(raw_col))

s = (s
    .withColumn("volume_masked", is_masked("volume_raw"))
    .withColumn("energy_masked", is_masked("energy_raw"))
    .withColumn("hours_masked", is_masked("hours_raw"))
    .withColumn("cast_failed_unexpected",
                unexpected_failure("volume_raw", "volume")
                | unexpected_failure("energy_raw", "energy")
                | unexpected_failure("hours_raw", "hours")))

display(s.agg(
    F.count("*").alias("rows"),
    F.sum(F.col("volume_masked").cast("int")).alias("volume_masked"),
    F.sum(F.col("hours_masked").cast("int")).alias("hours_masked"),
    F.sum(F.col("cast_failed_unexpected").cast("int")).alias("unexpected_cast_failures"),
    F.sum(F.when(F.col("CCICode").isNull(), 1).otherwise(0)).alias("cci_null_after_trim"),
    F.sum(F.when(F.col("well_key").isNotNull(), 1).otherwise(0)).alias("rows_with_well_key"),
))

