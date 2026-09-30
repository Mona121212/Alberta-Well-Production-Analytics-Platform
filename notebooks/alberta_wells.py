#!/usr/bin/env python
# coding: utf-8

# ## alberta_wells
# 
# null

# In[1]:


# Welcome to your new notebook
# Type here in the cell editor to add code!

import os, json, uuid, fnmatch, re
from datetime import datetime
import pandas as pd
from pyspark.sql import functions as F
from pyspark.sql.types import StructType, StructField, StringType

RUN_ID = str(uuid.uuid4())[:8]
BASE = "/lakehouse/default/Files"
ONLY_SOURCE = None      # only run the source to change "petrinex_vol" 

with open(f"{BASE}/config/sources.json") as fh:
    CONFIG = json.load(fh)
print("RUN_ID:", RUN_ID)

for src in CONFIG["sources"]:
    folder = f"{BASE}/{src['folder']}"
    files = [f for f in os.listdir(folder) if fnmatch.fnmatch(f, src["file_pattern"])]
    print(src["name"], "->", files)


def clean_cols(cols):
    return [re.sub(r"[ ,;{}()\n\t=]", "_", c.strip()) for c in cols]

def read_source_file(src, fname):
    rel = f"{src['folder']}/{fname}"
    if src["format"] == "csv":
        sdf = (spark.read.option("header", True)
               .option("inferSchema", False).csv(f"Files/{rel}"))
    elif src["format"] == "xlsx":
        pdf = pd.read_excel(f"{BASE}/{rel}", dtype=str)
        pdf = pdf.astype(object).where(pdf.notna(), None)
        schema = StructType([StructField(c, StringType(), True) for c in pdf.columns])
        sdf = spark.createDataFrame(pdf, schema=schema)
    else:
        raise ValueError(f"unsupported format: {src['format']}")
    return sdf.toDF(*clean_cols(sdf.columns))

def write_bronze(sdf, src, fname):
    tbl = src["target_table"]
    sdf = (sdf.withColumn("_source_file", F.lit(fname))
              .withColumn("_ingest_time", F.current_timestamp())
              .withColumn("_run_id", F.lit(RUN_ID)))
    if src["load_mode"] == "overwrite":
        (sdf.write.format("delta").mode("overwrite")
            .option("overwriteSchema", "true").saveAsTable(tbl))
    else:
        if spark.catalog.tableExists(tbl):
            spark.sql(f"DELETE FROM {tbl} WHERE _source_file = '{fname}'")
        sdf.write.format("delta").mode("append").saveAsTable(tbl)
    return spark.table(tbl).filter(F.col("_source_file") == fname).count()

LOG_SCHEMA = ("run_id string, layer string, source string, file string, "
              "rows_in long, rows_out long, status string, message string, "
              "started_at timestamp, ended_at timestamp")

def log_run(rows):
    (spark.createDataFrame(rows, LOG_SCHEMA)
          .write.format("delta").mode("append").saveAsTable("pipeline_run_log"))



for src in CONFIG["sources"]:
    if ONLY_SOURCE and src["name"] != ONLY_SOURCE:
        continue
    files = sorted(f for f in os.listdir(f"{BASE}/{src['folder']}")
                   if fnmatch.fnmatch(f, src["file_pattern"]))
    for fname in files:
        t0, rows_in, rows_out, status, msg = datetime.now(), None, None, "SUCCESS", ""
        try:
            sdf = read_source_file(src, fname)
            rows_in = sdf.count()
            rows_out = write_bronze(sdf, src, fname)
            if rows_in != rows_out:
                status, msg = "MISMATCH", "rows_in != rows_out"
        except Exception as e:
            status, msg = "FAILED", str(e)[:500]
        log_run([(RUN_ID, "bronze", src["name"], fname, rows_in, rows_out,
                  status, msg, t0, datetime.now())])
        print(src["name"], fname, rows_in, rows_out, status, msg)



display(spark.sql("SELECT * FROM pipeline_run_log ORDER BY started_at DESC"))

