# ST37 GIS Exploration and Repository Audit

Date: 2026-10-21. Branch: `feature/gis-layer`. Read-only exploration; no pipeline code or Lakehouse tables were changed.

Scripts that reproduce every number below are in [`scripts/explore/`](../scripts/explore/) (local Python, `pip install -r scripts/explore/requirements.txt`).

## 1. Source location

| Expected path            | Actual path                                         | Notes |
| ------------------------ | --------------------------------------------------- | ----- |
| `data/raw/ST37.gdb`      | `data/raw/aer_st37/ST_37_Wells.gdb`                 | The real File Geodatabase (1.7 GB). |
| -                        | `data/raw/aer_st37/ST_37_Wells.gdb/ST_37_Wells.gdb` | Nested stub with system tables only (~370 KB). GDAL cannot open it. Leftover from unzipping. |
| -                        | `data/raw/aer_st37/ST_37_Wells.gdb/ST_37_Wells.gdb_zip.zip` | 1.0 GB source archive stored inside the GDB folder. |
| -                        | `data/raw/aer_st37/SHP/`                            | Same data as shapefiles (2.9 GB, well geometry split by township range). |
| -                        | `data/raw/aer_st37/ST_37_Excel/`                    | Attribute-only extracts. The pipeline ingests these today. |

## 2. Layers

All four layers and all shapefiles use **NAD83 / Alberta 10-TM (Forest), EPSG:3400** (metres).

| Layer                                       | Geometry          | Rows    | Key (unique)            | Other key              |
| ------------------------------------------- | ----------------- | ------- | ----------------------- | ---------------------- |
| `ST37_Production_String_NAD83_10TM_AEP_Forest` | Point Z         | 658,266 | `Well_UWI`              | `Prod_String_UWI` (531,938 distinct), `Well_Licence_Number` |
| `ST37_Bottom_Hole_NAD83_10TM_AEP_Forest`    | Point             | 538,629 | `Well_UWI`              | `Well_Licence_Number` (485,458 distinct) |
| `ST37_Surface_Hole_NAD83_10TM_AEP_Forest`   | Point             | 539,890 | `Well_Licence_Number`   | `Licence_Surface_Location_Label` (DLS text, 351,895 distinct) |
| `ST37_Well_Geometry_NAD83_10TM_AEP_Forest`  | MultiLineString Z | 533,755 | `UWI_Label`             | `Well_Licence_Number`  |

Excel extracts have the same row counts and attribute columns as the GDB layers, minus geometry. **`ST37_BH.xlsx` already has `BH_Latitude`/`BH_Longitude`, and `ST37_SH.xlsx` has `SH_Actual_Latitude`/`SH_Actual_Longitude`.**

### Sample values

| Layer             | UWI / licence                                | Location                                              |
| ----------------- | -------------------------------------------- | ----------------------------------------------------- |
| Production_String | `00/06-06-001-01W4/0`, licence `0065081`     | `POINT Z (855690.68 5437210.63 855.9)`                |
| Production_String | `F1/06-06-001-01W4/0`, licence `0078503`     | same XY, Z = -352.6 (second event at the same site)   |
| Bottom_Hole       | `00/08-09-051-25W4/0`, licence `0000001`     | `POINT (591882.89 5913802.66)`, lat 53.386619, lon -113.617905 |
| Surface_Hole      | licence `0000001`, label `08-09-051-25W4`    | `POINT (591882.89 5913802.66)`, lat 53.386619, lon -113.617905 |
| Well_Geometry     | `00/06-06-001-01W4/0`, source `Calculated`   | `MULTILINESTRING Z` with SHAPE_Length 0 (vertical stub) |

## 3. Keys and joins

**UWI format.** ST37 uses `LE/LS-SC-TWP-RGW M/E`, e.g. `00/06-06-001-01W4/0`. The current Silver regex
`^(\w{2})/(\d{2})-(\d{2})-(\d{3})-(\d{2})W(\d)/(\d+)$` matches **100%** of UWIs in all three UWI-keyed layers,
including alphanumeric location exceptions (`AA` 31,307, `AB` 6,611, `W0` 5,594, `S0` 2,811, `F1`, ...).
It converts them to the Petrinex 16-character `well_key` (`1` + LE + LSD + SEC + TWP + RGE + `W` + MER + 2-digit event),
e.g. `00/06-06-001-01W4/0` -> `100060600101W400`.

**Coverage between layers (by UWI):**

| Check                                      | Result |
| ------------------------------------------ | ------ |
| Production_String and Bottom_Hole          | 531,911 shared; 126,355 only in PS; 6,718 only in BH |
| Well_Geometry within Bottom_Hole           | 533,755 of 533,755 (100%); 4,874 BH wells have no trajectory |
| PS licence found in Surface_Hole           | 100% (after trimming) |

**Grain.** Surface_Hole has one row per licence (surface pad). One licence can have many UWIs (legs and events),
so surface location joins to wells through `Well_Licence_Number`, not UWI.

**Prod_String_UWI vs Well_UWI.** 126,329 PS rows have a different value in the two columns, e.g.
`Prod_String_UWI = 00/08-35-001-01W4/2` for `Well_UWI` `/0`, `/3`, `/4`, `/5`. `Well_UWI` is the unique
event-level key and is the right join key to Petrinex. `Prod_String_UWI` groups commingled events.

## 4. Data quality findings

| ID     | Finding                                                                                       | Impact | Suggested handling |
| ------ | --------------------------------------------------------------------------------------------- | ------ | ------------------ |
| GIS-01 | `Status_Fluid` / `Status_Mode` / `Status_Type` mix two encodings: numeric codes (`2`, `10`, `0`) on some rows, padded text (`ABANDONED ZONE`, `FLOWING`) in `Status_Mode` with `0` elsewhere on others. Seen in sampled rows where `Prod_String_UWI != Well_UWI`; full extent not yet quantified. | `status_fluid` etc. in `silver_dim_well` are not usable as a slicer as-is. | Quantify per pattern; decode codes with an AER lookup in config; add an accepted-values DQ rule. |
| GIS-02 | `Well_Licence_Number` is padded with trailing spaces in Production_String (649,471 rows), not in other layers. Licences are 7-char zero-padded and can carry a letter suffix (`0004123L`). | Licence join fails without trimming. | Already covered by `normalize_strings`; keep as string, never cast to int. |
| GIS-03 | Date formats differ by layer: `YYYYMMDD` (`Final_Drill_Date`, `Spud_Date` in BH/SH), `MM/DD/YYYY` (`Licence_Status_Date` in BH/SH), real datetimes in PS. | Naive `to_date` gives NULLs or wrong dates. | Per-column format in config; cast-failure DQ rule like D-004. |
| GIS-04 | 341,799 of 533,755 trajectories (64%) have `SHAPE_Length = 0` (341,782 `Calculated`, 17 `Surveyed`). | Trajectory layer is mostly degenerate vertical stubs. | Low value for the MVP map; defer. |
| GIS-05 | Bottom-hole extent slightly exceeds the provincial box (lat min 48.9975, lon min -120.0028). | A strict "inside Alberta" rule would reject real wells. | Use a tolerance (for example 0.01 deg) or warning severity. |
| GIS-06 | Several Production_String events share one XY with different Z values. | Many identical map points per site. | Map at well or surface-pad grain, not per event. |
| GIS-07 | 126,355 PS UWIs have no Bottom_Hole row; 6,718 BH UWIs have no PS row. | Bottom-hole lat/long missing for some producing wells. | Fall back to surface location via licence (100% coverage). |

**Coordinates verified.** On 20,000 sampled bottom holes, reprojecting the EPSG:3400 geometry to NAD83 (EPSG:4269) matches
the `BH_Latitude`/`BH_Longitude` attributes within 1.25e-9 deg. No nulls in any lat/long column.
So the Excel lat/long columns are trustworthy and the GDB is **not** required to place wells on a map.
NAD83 vs WGS84 differs by about 1-2 m in Alberta, which is negligible for Power BI maps.

## 5. Repository audit

### Current data flow

```
data/raw (local, gitignored) --manual upload--> Lakehouse Files/raw
  01_bronze_transform.py : config/sources.json -> bronze_petrinex_vol (append by file),
                           bronze_st37_production_string, bronze_st37_bh (overwrite), pipeline_run_log
  02_silver_transform.py : silver_petrinex_vol, silver_dim_well, dq_results, rejected_records,
                           gold_dim_well, gold_fact_production, reconciliation_results, dq_rule_test_results
```

Gold today: `gold_fact_production` (grain month x ReportingFacilityID x well_key x ProductID; PROD + WI rows only)
and `gold_dim_well` (silver dim + placeholder rows). No date, product, operator or facility dimension. No unit column.

### Bugs and code quality issues

| ID    | Location | Issue |
| ----- | -------- | ----- |
| A-01  | `02_silver_transform.py` cells In[1] / In[7] | The notebook is an exported exploration session with out-of-order cells. Run top to bottom (as `fab job run` does), In[1] writes Silver **without** the `*_raw` / `*_masked` columns (reintroducing D-006), then In[7] raises `ValueError` because `s` lacks them. The job cannot complete. |
| A-02  | `02_silver_transform.py` In[1], In[8], In[15] | Three copies of the Silver cleaning logic, two run IDs (`RUN_ID`, `SILVER_RUN_ID`). Silver rows and `dq_results` carry different run IDs, so lineage cannot be joined. |
| A-03  | `02_silver_transform.py:386-390` | `gold_volume_totals_match_silver_by_product` compares `fact_rows` with the table just written from `fact_rows`, so it can never fail. No Bronze -> Silver volume sum check. |
| A-04  | Both notebooks | Failed DQ or reconciliation tests are written as `FAIL` but never raise. Bronze catches all exceptions. The job reports success on bad data. |
| A-05  | `02_silver_transform.py:107` | `silver_dim_well` uniqueness on `well_key` is printed but not asserted. It holds today (`Well_UWI` is unique in both PS and BH), but a duplicate would silently fan out fact rows through the left join at line 293. |
| A-06  | `02_silver_transform.py:296-311` | DQ rules are hard-coded in the notebook; CLAUDE.md requires config-driven rules. In[12] creates rule names with dates in them (`wells_disappeared_2026-05-01_to_...`). |
| A-07  | `01_bronze_transform.py:61`, `02_silver_transform.py:65` | Delete-then-append is not atomic and builds SQL from file names. `replaceWhere` would do both safely. |
| A-08  | `01_bronze_transform.py` | Leftover Fabric boilerplate (`Welcome to your new notebook`, `# null`, title `alberta_wells`), hard-coded `BASE`, file listing loop duplicated. |
| A-09  | `config/sources.json` | Folder `raw/aer_st37` differs from the repo layout (`aer_st37/ST_37_Excel`); Lakehouse upload layout is undocumented. SH and Well_Geometry are not configured. |
| A-10  | Silver | Mixed naming: raw PascalCase (`ProductID`, `ReportingFacilityID`) next to snake_case (`well_key`, `volume`) flows into Gold. |
| A-11  | `02_silver_transform.py:291` | Exact-duplicate window partitions by every business column (about 40). Acceptable for 2 months; expensive for 12. A hash column would be cheaper. |
| A-12  | Repo | Gold lives in the Silver notebook; no `DECISIONS.md`, no TMDL semantic model, no tests folder; README is two lines. `docs/defect_log.md` describes 2 loaded months while 12 files are in `data/raw/petrinex`. |

### How new DQ rules plug in today

Rules are `(name, severity, condition)` tuples in the `rules` list (In[9]). `reject` rows go to `rejected_records`
and are excluded from Gold by `reject_cond` (In[10]); `warning`/`info` are only counted in `dq_results`.
In[11] tests rules against constructed rows. A GIS rule (for example `well_missing_location`) would be one more tuple,
but the list should move to config first (A-06).

## 6. Recommended GIS integration (summary)

1. Ingest `ST37_SH.xlsx` into Bronze through `config/sources.json` (BH is already ingested and has lat/long). No GDAL needed in Fabric.
2. In Silver, add `surface_latitude/longitude` (licence join to SH) and `bh_latitude/longitude` (UWI join to BH) to `silver_dim_well`, plus parsed DLS parts (meridian, range, township, section).
3. Add DQ rules: location not null for producing wells (warning), coordinates inside Alberta with tolerance (warning), licence found in SH (referential).
4. Gold: expose map coordinates on the well dimension for the Power BI Azure Maps visual.
5. Defer trajectories (Well_Geometry) and the GDB: 64% are zero-length, and Direct Lake cannot render WKT lines.

Approved decisions are recorded in [`DECISIONS.md`](../DECISIONS.md) (DEC-001 to DEC-005).
