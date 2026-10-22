# Design Decisions

Each entry: Context / Decision / Alternatives / Consequences. Newest at the bottom.

---

## DEC-001 Map point for each well: surface location first, bottom hole as fallback

**Date:** 2026-10-21

**Context.** The Power BI map needs one point per well. ST37 provides a surface location per licence
(`Surface_Hole`, 100% of Production_String licences match) and a bottom-hole location per UWI
(`Bottom_Hole`, 126,355 Production_String UWIs have no bottom-hole row). Several events share one surface pad.

**Decision.** `gold_dim_well.map_latitude/map_longitude` = surface location (joined by licence number);
if missing, bottom-hole location (joined by UWI). `location_source` records which one was used
(`surface`, `bottom_hole`, or NULL). Both surface and bottom-hole coordinates are also kept as separate columns.

**Alternatives.**
- Bottom hole first: closer to the producing zone, but lower coverage and horizontal wells would scatter around the pad.
- Production_String point: per-event, many identical points per site, and only available in the GDB/shapefiles.

**Consequences.** Full location coverage for producing wells; wells on one pad stack on one map point.
Horizontal-well reach is not shown (see DEC-003).

---

## DEC-002 Coordinate source: Excel lat/long for Gold, GDB used only for local QA

**Date:** 2026-10-21

**Context.** Coordinates exist in two forms: lat/long attribute columns in the ST37 Excel extracts
(`ST37_SH.xlsx`, `ST37_BH.xlsx`), and projected geometry (EPSG:3400) in the File Geodatabase and shapefiles.
Fabric Spark has no GDAL by default. A 20,000-row sample showed the Excel-equivalent attributes match the
reprojected GDB geometry within 1.25e-9 degrees.

**Decision.**
- Gold uses the Excel lat/long (NAD83, EPSG:4269) through the existing Bronze -> Silver path.
- A local GIS step (`scripts/gis/`) converts the GDB to GeoPackage and cross-checks GDB geometry against Excel
  lat/long per well. It is a QA rule: mismatches above a tolerance are written to a report and listed in the defect log.

**Alternatives.**
- Ingest the GDB into Fabric: needs GDAL/geopandas in the Spark environment, 1.7 GB input, and adds no coordinates the Excel files lack.
- Excel only, no cross-check: simpler, but no evidence that the attribute coordinates are correct.

**Consequences.** No geospatial dependency in Fabric. The QA step runs locally and is manual, so it must be re-run
when ST37 is refreshed. NAD83 vs WGS84 differs by about 1-2 m in Alberta, which is accepted for mapping.

---

## DEC-003 Well trajectories are out of the MVP

**Date:** 2026-10-21

**Context.** `ST37_Well_Geometry` holds 533,755 3D trajectories; 341,799 (64%) have zero length.
Power BI Direct Lake map visuals cannot render WKT line geometry.

**Decision.** Do not load trajectories into the Lakehouse for the MVP.

**Alternatives.** Load as WKT for a future Fabric Real-Time / ArcGIS visual; simplify to surface-to-bottom-hole segments.

**Consequences.** The map shows points only. Listed under next steps in the README.

---

## DEC-004 Fix the Silver notebook before building Gold, in a separate commit

**Date:** 2026-10-21

**Context.** `notebooks/02_silver_transform.py` is an exported exploration session: out-of-order cells,
three copies of the cleaning logic, two run IDs, and it cannot complete a top-to-bottom run
(see `docs/st37_gis_exploration.md`, A-01 to A-05). Gold currently lives in the same notebook.

**Decision.** Rewrite Silver as a linear notebook that runs end to end, keep the existing rule behaviour and
outputs, and review it before any Gold work. Gold moves to its own notebook (`03_gold_transform`) afterwards.

**Alternatives.** Build Gold on top of the current notebook and clean up later.

**Consequences.** Gold work waits for the Silver review. Existing passing tests must still pass after the rewrite.

---

## DEC-005 Gold star schema

**Date:** 2026-10-21

**Context.** Gold today is one fact and one well dimension, with raw PascalCase columns and no date or product
dimension. The report needs MoM and rolling 3-month measures and must not sum volumes across products with different units.

**Decision.** Build in `03_gold_transform`:
- `gold_fact_production`: grain month x facility x well x product (PROD activity, WI rows).
- `gold_dim_well` (unique `well_key`, with location columns from DEC-001), `gold_dim_date` (month grain with `month_index`),
  `gold_dim_product` (with `unit`), `gold_dim_operator`.
- snake_case column names in Gold.
- Gold DQ: unique dimension keys, no orphan fact keys, producing well has a map location (warning),
  coordinates inside Alberta with tolerance (warning).

**Alternatives.** Keep a flat wide table for Power BI; snowflake facility -> operator.

**Consequences.** More tables to reconcile, but standard star schema for Direct Lake and clean time-intelligence measures.

---

## DEC-006 GIS track: GDB as source, surface holes as "the wells" layer

**Date:** 2026-10-22

**Context.** The GIS track (local Python under `gis/`) needs one wells layer for exports, QA and client packages.
ST37 ships the same data as a File Geodatabase, Shapefiles and Excel. The Shapefile DBF truncates field names
to 10 characters and drops `Licence_Surface_Location_Label` (the DLS text) from the surface-hole layer.

**Decision.** Read the GDB (all 4 layers). "Wells" = `ST37_Surface_Hole` (one row per licence, surface
location, DLS label, licensee, licence status). Bottom holes, production strings and trajectories go into the
same GeoPackage as supporting layers. Fields are renamed to snake_case through `config/gis.yaml`.

**Alternatives.**
- Shapefiles: no DLS label, truncated names, trajectories split into 4 files.
- Bottom hole as the wells layer: per UWI, so licence-level duplicate and DLS checks would not apply.

**Consequences.** One well per licence; multi-leg wells share one point. The Shapefile export renames fields
again (`exports.shp_field_names`) to fit the 10-character limit.

---

## DEC-007 Stage the GDB to GeoParquet once; sample mode for development

**Date:** 2026-10-22

**Context.** The GDB is 1.7 GB (mostly trajectories). Reading it on every development run is slow.

**Decision.** `gis.stage` copies each GDB layer and the Alberta boundary to GeoParquet, untouched, once per mode;
later steps read only Parquet. `run.mode: sample` reads a Calgary-region bbox (capped at `max_rows` per layer);
`full` reads everything. Outputs go to `gis/output/<mode>/` so the two never mix. The boundary is never filtered.

**Alternatives.** Read the GDB in every step; sample by first N rows (would cluster in township 1, far from Calgary,
and break joins between layers).

**Consequences.** A bbox sample keeps the layers consistent with each other (same wells in every layer).
Staging must be re-run with `--force` when the raw ST37 files are refreshed.

---

## DEC-008 Licence status standardization through a mapping table

**Date:** 2026-10-22

**Context.** ST37 `Licence_Status` has 9 raw values, including the case variant `Re-entered` (2 rows) next to
`Re-Entered`, and abbreviations such as `RecCertified` and `RecExempt`.

**Decision.** `config/gis_licence_status_map.csv` maps each exact raw value to a display value and a status group
(Active, Inactive, Abandoned, Reclaimed, Cancelled). The raw value is kept as `licence_status_raw`.
Unmapped values become `Unmapped` and are logged.

**Alternatives.** Case-insensitive matching in code: hides the source defect and is less transparent.

**Consequences.** A new AER status value shows up as `Unmapped` instead of failing the run; the QA step reports it.

---

## DEC-009 GIS QA framework: config-driven rules, three severities, FAIL stops the pipeline

**Date:** 2026-10-22

**Context.** The GIS track needs repeatable spatial QA with results that match the Lakehouse `defect_log`
schema, examples a reviewer can follow, and a hard stop when the deliverable would be wrong.

**Decision.** Rules live in `config/gis_qa.yaml` (id, check function, layer, severity, params);
`gis/qa_checks.py` holds one generic function per check type, so a new rule on another layer is config only.
Severities: FAIL (stops the pipeline when failures exceed `max_failures`, after all outputs are written),
WARN (counts against a well in the scorecard), INFO (known source characteristics or approximate checks).
FAIL is reserved for problems that make the GIS deliverable wrong: surface hole outside Alberta, geometry vs
lat/long mismatch, duplicate keys, missing key or geometry. Key lookups (referential integrity, duplicates)
use full-province key tables staged without geometry, so the sample bbox never creates false orphans.
A well's QA result is licence-level: a flag on its bottom hole or production string counts against it.
`surface_licence_without_wellbore` excludes Cancelled licences (all 264 sample cases were cancelled and never spudded).

**Alternatives.** Hard-coded checks in one script (the A-06 problem in Silver); Great Expectations
(heavy for 22 rules and weak on geometry).

**Consequences.** `max_failures` thresholds for FAIL rules may need tuning after the first full run.
Rule tests with constructed rows are in `tests/gis/`.

---

## DEC-010 DLS check uses an approximate township grid, not an ATS layer

**Date:** 2026-10-22

**Context.** Checking `Licence_Surface_Location_Label` (LSD-SEC-TWP-RGE W MER) against the point needs the
Alberta Township System grid. No ATS polygon layer is in the repo (AltaLIS ATS is a separate licensed download).

**Decision.** Compute a continuous grid position from the point's NAD83 lat/long:
township = (lat - 49) / 0.0873 deg; range = ground distance west of the labelled meridian (110/114/118 W,
measured along the parallel on GRS80) / 9,790 m. Report how far the point lies outside the labelled cell, in
cell units. Section: position inside the labelled township in sixths, with DLS boustrophedon numbering
(1-6 east to west on the south row, 7-12 back east, ...). Constants were fitted on all 539,890 surface holes:
at 0.25-cell tolerance only 1 well fails township/range; at 0.5-section tolerance 853 fail the section check.

**Alternatives.** Download the AltaLIS ATS grid and do a point-in-polygon join (exact, adds a licensed
dependency); compare against the median position of all wells with the same label (circular).

**Consequences.** Township/range is reliable enough for WARN. Section is INFO only: correction-line jogs and
road allowances make the approximation off by up to about half a section. LSD is not checked.
Replacing the grid with ATS polygons later only changes `dls_positions` in `gis/qa_checks.py`.

---

## DEC-011 Trajectory and status findings that shaped the QA rules

**Date:** 2026-10-22

**Context.** Earlier exploration (GIS-04) read `SHAPE_Length = 0` as degenerate trajectories. Profiling showed
`SHAPE_Length` is 2D: every zero-length trajectory sampled is a two-vertex vertical line whose Z range equals total
depth. `Status_Mode` mixes numeric codes (495,586 rows) and text (162,680); `Status_Fluid` and `Status_Type` are
always codes. The Excel extract stores text for all three, and the code-to-text pairs are not one-to-one
(GDB mode `2` = `ABANDONED` 2,598 times but `ABANDONED ZONE` 13 times in the first 5,000 rows).

**Decision.** Two trajectory rules: zero 3D length (WARN, truly degenerate) and zero 2D length (INFO, vertical,
no lateral survey). Status consistency is checked only where `Status_Mode` is text (producing mode on an abandoned,
reclaimed or cancelled licence). GDB vs Excel differences are INFO, with every value pair written to
`gdb_vs_excel_value_pairs.csv`.

**Alternatives.** Derive a code lookup from the Excel pairs: rejected, the pairs conflict.

**Consequences.** Numeric status codes stay undecoded until an official AER code table is added to `config/`.

---

## DEC-012 Liability screening: working definitions, not AER regulatory definitions

**Date:** 2026-10-22

**Context.** Wave 3 screens for abandoned-not-reclaimed wells, inactive candidates and a licensee profile.
AER defines inactive wells, suspension and closure requirements in its own directives (for example
Directive 013 and Directive 020), with criteria that depend on well type and risk. Those criteria were not
sourced for this project, so none of their thresholds are used or claimed.

**Decision.** Working definitions, all in `config/gis_liability.yaml`, at licence grain (one surface hole):
- Status group comes from the licence status mapping (DEC-008): Active, Inactive (Suspension), Abandoned,
  Reclaimed (RecCertified, RecExempt), Cancelled.
- **Abandoned not reclaimed** = licence status `Abandoned`. Age = years from spud to the end of the as-of month;
  years in status = years since `Licence_Status_Date` (assumed to be the date the current status was set).
- **Activity** = a Petrinex well row with PROD, INJ or DISP and volume > 0, applying the Gold reject rules
  (masked or negative volume, hours above the month maximum). INJ and DISP are included because service wells
  never report PROD. A licence is active in a month if any of its UWIs (production strings or bottom holes,
  mapped with the Silver UWI -> well key rule) has activity.
- **Inactive candidate** = Active licence group, spudded, no activity in the last 6 (or 12) months up to the
  latest Petrinex month. Wells spudded inside a window are not flagged for that window. `petrinex_evidence`
  separates "no Petrinex record" from "reported without volume (e.g. SHUTIN)".
- **Licensee profile:** active = reporting or no activity for 6 months only; inactive = no activity for 12 months
  plus Suspension; inactive_ratio = inactive / (active + inactive); unreclaimed_ratio = abandoned /
  (abandoned + reclaimed). Ratios are blank under 10 wells. Licensee = ST37 licensee (the licence holder),
  not the Petrinex operator.

**Alternatives.** Use AER inactive-well criteria: needs sourced, version-dated rules per well type.
Use Status_Mode codes: undecoded (DEC-011).

**Consequences.** Outputs are a screening list for review, not a compliance finding. Known false positives:
wells that are non-producing by design and never report to Petrinex (for example 74 licences of one
non-oil-company licensee on a single pad in the sample), domestic water wells and historic wells held by individuals.

---

## DEC-013 Rebuild Gold-equivalent well activity locally from raw Petrinex files

**Date:** 2026-10-22

**Context.** Wave 3 joins wells to production. Gold (`gold_fact_production`) lives only in the Fabric Lakehouse;
there is no local copy and the GIS track runs locally.

**Decision.** `gis.stage` stages the WI rows of the 12 raw Petrinex CSVs (the same files Bronze ingests) and
`gis.liability` applies Gold's filters (PROD, WI, reject rules). A reconciliation test compares June 2026 with the
Fabric results in `docs/defect_log.md`: 161,098 distinct well keys and 135,154 producing wells; both match exactly.
A failed reconciliation test stops the pipeline.

**Alternatives.** Export Gold from Fabric with `fab` (needs an interactive login and a sync step on every run);
query the SQL endpoint (adds credentials to a local tool).

**Consequences.** No Fabric dependency for the GIS track. If the Silver/Gold reject rules change, the local copy
must change too; the reconciliation test will catch a drift in well counts.
