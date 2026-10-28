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

---

## DEC-014 Review class for large multi-licence pads that never reported

**Date:** 2026-10-22

**Context.** The first liability run flagged 74 licences of one non-oil-company licensee on a single pad
(same spud date, never in Petrinex) as inactive candidates. Wells that are not production wells never report to
Petrinex, so they inflate the inactive list and distort the licensee ranking.

**Decision.** Surface holes within `pads.distance_m` (50 m, single linkage) form a pad. A well that would be an
inactive candidate becomes `review_multi_licence_pad_never_reported` when its pad has at least
`pad_review.min_licences` (10) non-cancelled licences and no licence on the pad has any Petrinex record.
The class is reported separately (`review` in the licensee profile) and is not an inactive candidate.

**Alternatives.** Exclude by licensee name (not reproducible); exclude every well never in Petrinex
(would hide real long-inactive wells).

**Consequences.** Full province: 421 licences move to review (sample: exactly the 74-licence pad). Small
never-reported pads stay ordinary candidates. The threshold is a project choice, not a regulatory number.

---

## DEC-015 GQA-01 tolerates 13 known border cases on the meridian borders

**Date:** 2026-10-22

**Context.** The first full run stopped on GQA-01: 13 surface holes are more than 100 m outside the StatCan 2021
cartographic boundary. 11 lie at about 110.0057 W, labelled range 01 W4, 110-160 m east of the StatCan line; 2 lie
west of 120 W (213 m, a suspended well, and 571 m, a cancelled licence). Their lat/long attributes match the geometry
(GQA-03 passes) and their DLS labels are border ranges. The StatCan line itself is not on 110 W: it runs at
110.0076 W at 50.23 N and 110.0033 W at 55 N, consistent with a boundary that follows a surveyed meridian; this was
not checked against an authoritative legal boundary.

**Decision.** Keep GQA-01 at FAIL with the 100 m tolerance and set `max_failures: 13`, so any new outlier stops the
pipeline. The 13 stay listed in the QA outputs.

**Alternatives.** Raise the tolerance to 600 m (would hide real errors anywhere in the province); downgrade to WARN
(loses the stop); use a different boundary (AltaLIS provincial boundary, not downloaded).

**Consequences.** The count is tied to the current ST37 snapshot: a refresh that adds a border well will stop the run
and need a review. Next step: compare against an authoritative Alberta boundary.

**Revision (2026-10-23): count replaced by an allowlist.** `max_failures: 13` accepted *any* 13 failures, so a new
error could hide behind a fixed border well, and the injected-bad-row demo passed GQA-01 as WARN. GQA-01 now has an
`allowlist` in `config/gis_qa.yaml`: the 13 licence numbers, each with a reason (licensee, status, DLS label, distance
and side of the line). The QA engine marks allowlisted rows in `qa_failures.csv` (`allowlisted` column) and
`qa_rule_summary.csv` (`rows_allowlisted`); a FAIL rule is FAIL as soon as one failure is not on its allowlist, and
WARN when all failures are allowlisted. Allowlist entries without a reason are rejected at config load; entries that
no longer fail are logged (warning in full mode). `max_failures` is removed. Alternatives: keep the count (rejected
for the reason above); allowlist by UWI or coordinates (licence number is the record key of the surface layer and
stable across ST37 releases). Consequence: a fixed or removed border well needs its entry deleted, and a new border
well needs a reviewed entry before the run passes.

---

## DEC-016 Spatial analysis units: approximate townships, 5 km hexagons, pads, lateral classes

**Date:** 2026-10-22

**Context.** Phase B summarizes wells by area without an ATS township layer and without a licensed grid product.

**Decision.**
- **Wells counted** = drilled licences (not cancelled, spud date present): 481,347 of 539,890.
- **Township polygon** = convex hull of the township's surface holes (key from the DLS label); townships with
  fewer than 3 wells or collinear wells get a 250 m buffer. This is the footprint of the wells, not the surveyed
  township (about 93 km2); `polygon_method` records which method was used.
- **Hexagons** = pointy-top cells, 5 km between neighbouring centres (21.65 km2), every cell touching Alberta,
  computed with axial coordinates (no spatial join). Volumes are 12-month Petrinex production of the wells in the cell.
- **Pads** = surface holes within 50 m, single linkage (`gis/spatial.py`); pad point = mean position; spread = twice the
  largest distance from that point.
- **Lateral class** per bottom hole (wellbore): surface-to-bottom-hole offset < 50 m vertical, >= 500 m horizontal,
  otherwise deviated. Project working definition, not an AER classification. Trajectory 2D length is reported next to it.

**Alternatives.** AltaLIS ATS polygons (licensed, not downloaded); H3 hexagons (extra dependency, cells not aligned to
the projected CRS); DBSCAN for pads (needs scikit-learn and a min-samples choice).

**Consequences.** Township areas and densities are indicative only. All four layers reconcile to the drilled-well count
and to total 12-month oil volume.

---

## DEC-017 Client data package format

**Date:** 2026-10-22

**Context.** A GIS analyst delivers well data to clients by licensee, township list or area of interest. The package
must open styled in QGIS, be readable without GIS software, and explain every field and known limitation.

**Decision.** `python -m gis.package` writes `deliverables/<name>/` with `data.gpkg` (wells, pads, qa_flags table,
package_extent, alberta_boundary), `wells.csv` (with EPSG:3400 x/y), `data_dictionary.md`, `README.md` (as-of dates,
CRS, contents, class counts, QA summary, sources, known issues) and one `.qml` per spatial layer. Styles are also
embedded as default styles in the GeoPackage `layer_styles` table (registered in `gpkg_contents`), so QGIS applies them
on open; verified with PyQGIS 3.44. Field descriptions come from `config/gis_field_dictionary.yaml`, and the build fails
if a packaged field has no description. Styles are generated as XML (`gis/styles.py`), so packaging does not need QGIS.
Packages contain derived data only; the data files are git-ignored, while README, dictionary and styles of the three
example packages are committed.

**Alternatives.** Shapefile + PDF (field-name truncation, no embedded styles); one style file per package without
embedding (users must load it by hand).

**Consequences.** Packages are reproducible from the pipeline outputs in about 100 s for the three largest licensees.
A new packaged field needs a dictionary entry first.

---

## DEC-018 Change detection on licence-level snapshots

**Date:** 2026-10-22

**Context.** Maintaining mapping layers means knowing what changed between ST37 releases. Only one ST37 snapshot exists.

**Decision.** `python -m gis.changes snapshot` saves the standardized surface holes (key attributes + geometry) as
`gis/output/snapshots/<mode>/wells_<date>.parquet`. `compare --old --new` keys on licence number and reports
new_licence, removed_licence, status_change, licensee_transfer, dls_label_change and moved_location (> 1 m in EPSG:3400),
as `changelog.csv`, `change_summary.csv` and `changes.gpkg` (points; moved wells also as old -> new lines). Without a
second release, `demo` copies the newest snapshot, injects a seeded, known set of changes into disjoint licences and
fails unless exactly those changes are found.

**Alternatives.** Row hashing of every column (reports noise such as re-formatted dates without saying what changed);
comparing raw GDBs (1.7 GB each, untyped fields).

**Consequences.** Real use: after downloading a new ST37 release, run the pipeline in full mode, take a snapshot with
that release date, then compare it with the previous snapshot. Changes in other layers (bottom holes, production
strings) are not tracked yet.

---

## DEC-019 Maps rendered with PyQGIS print layouts; classes shown per hexagon

**Date:** 2026-10-22

**Context.** Phase D needs a styled QGIS project and five PNG maps with title, legend, scale bar, north arrow and a
source/CRS note. PyQGIS runs in QGIS's own Python (3.12, no geopandas), separate from the project venv.

**Decision.** `gis/maps.py` (venv) prepares map layers, writes `.qml` styles from `config/gis_styles.yaml` and a JSON
spec; `gis/qgis_maps.py`, started with QGIS's `python-qgis-ltr.bat`, builds `gis/alberta_wells.qgz` (one print layout per
map, editable in QGIS) and exports `docs/maps/*.png` at 150 dpi. Styles are applied with `importNamedStyle` because
`loadNamedStyle` prefers a style embedded in the GeoPackage. The screening-class map shows the most common class among
drilled wells per 5 km hexagon: plotting 540,000 points at province scale lets whichever class is drawn last hide the
others.

**Alternatives.** matplotlib/geopandas maps (no editable QGIS project, weaker cartography); points with a draw order
(tried: abandoned wells hid 148,479 active wells).

**Consequences.** Map rendering needs a local QGIS install (path in `config/gis_maps.yaml`); `gis.run` skips the maps
step with a warning when it is missing. The project references outputs under `gis/output/full/` and `deliverables/`,
so it opens fully only after a full run.

---

## DEC-020 QGIS output: hide one exact known message, surface every other ERROR

**Date:** 2026-10-23

**Context.** Each PNG export prints `ERROR 6: The PNG driver does not support update access to existing datasets.`
(QGIS tries to write georeferencing back into the finished PNG; the image is complete). The maps step hid it, plus a
GDAL "Warning 1: Non-conformant" message, by prefix match, so any other `ERROR 6: The PNG driver ...` text would also
have been hidden.

**Decision.** `config/gis_maps.yaml` lists `ignored_qgis_messages`; a line is hidden only when it matches one entry
exactly (after trimming whitespace). Any other line starting with `ERROR` is logged at WARNING level; other lines at
INFO. The number of hidden lines is logged. The Non-conformant warning is no longer hidden.

**Alternatives.** Prefix or regex match (hides variants we have not seen); disable the QGIS world-file/georeference
export (depends on QGIS version-specific layout settings).

**Consequences.** A new or changed GDAL message shows up in the run log; if it is reviewed as harmless, it is added
to the config list verbatim.

---

## DEC-021 Split Gold into 03_gold_transform; pass DQ flags through a separate flag table

**Date:** 2026-10-25

**Context.** Gold was built in the last cells of `02_silver_transform` from in-memory variables (`base`, `rules`,
`flagged`), and the date and operator dimensions lived in a third notebook, `03_gold_dims`. Gold could not be rerun
without rerunning Silver and DQ, and the notebook boundaries did not match the medallion layers (DEC-004).
A separate Gold notebook needs two row-level facts from DQ: whether a row is rejected, and whether it is in scope
(PROD activity, WI rows). Both depend on the whole table: `exact_duplicate` is a window over every business
column, and the well check joins `silver_dim_well`.

**Decision.** `02_silver_transform` writes `silver_petrinex_vol_flagged` from the same persisted DataFrame that the
DQ counts and `rejected_records` use: every Silver column plus `dq_rejected`, `dq_in_scope` and `_dq_run_id`,
overwritten on every run (never appended). `03_gold_transform` reads only Lakehouse tables, takes `fact_rows` as
`~dq_rejected & dq_in_scope`, and reuses `_dq_run_id` as its run ID, so `dq_results` and `reconciliation_results`
still share one `run_id`. It stops if the table holds more than one run ID or its row count differs from
`silver_petrinex_vol` (Silver reloaded without rerunning DQ). Business logic, Gold columns and the five
reconciliation tests are unchanged.

**Alternatives.**
- New columns on `silver_petrinex_vol`: Silver is reloaded per source file before DQ runs, and the flags depend on
  the whole table, so every run would have to rewrite all of Silver after DQ. That changes the Silver load mode and
  rewrites the table being read.
- A slim table with only keys and flags: Silver has no row ID, and exact duplicates are identical in every business
  column, so flags cannot be joined back to one specific copy.
- Recompute the rules in `03_gold_transform`: two copies of the rule logic that can drift apart.

**Consequences.** One more Silver-sized table, rewritten on every run. Gold can be rerun alone from stored flags;
rerunning `03_gold_transform` without `02_silver_transform` appends a second set of reconciliation rows under the same
`run_id`. Which copy of an exact duplicate is kept is still arbitrary (`row_number` over `lit(1)`), as before; the
copies are identical in business columns. The rest of DEC-005 (snake_case Gold columns, month-grain
`gold_dim_date`, `gold_dim_product`) is not done yet; this change is structure only.
Verified in Fabric on 2026-10-27: `02_silver_transform` and `03_gold_transform` ran without errors and all five
reconciliation tests passed (6,481,961 Silver rows, 2,908,544 `gold_fact_production` rows). The four Gold tables
moved from Delta versions 5/5/1/1 to 6/6/2/2, and `scripts/verify/compare_gold_versions.sql` found 0 different rows
in both directions of EXCEPT ALL for every table, excluding the run ID columns (`_gold_run_id`, `_silver_run_id`).

---

## DEC-022 Stop the pipeline when a test fails

**Date:** 2026-10-27

**Context.** The five reconciliation tests in `03_gold_transform` and the DQ rule tests in `02_silver_transform`
(`dq_rule_test_results`) only wrote PASS or FAIL rows. A FAIL did not stop the notebook, so a scheduled run finished
as "Succeeded" and nobody looked at the results. In `02_silver_transform` the rule tests run after
`silver_petrinex_vol_flagged` is written, so stopping 02 alone would still leave flags from an untested run for
`03_gold_transform` to use.

**Decision.**
- `03_gold_transform` appends its results to `reconciliation_results` first, then raises a `ValueError` that names
  every failed test with its expected and actual values.
- `02_silver_transform` writes `dq_rule_test_results` (now with `run_id`, overwritten on every run), then raises if any
  rule test failed. The rule tests check the rule code on constructed rows, so a failure means the reject flags cannot
  be trusted.
- `dq_run_log` is written only in the last cell of `02_silver_transform`, so it marks a run that finished with all
  rule tests passed. `03_gold_transform` stops if the `_dq_run_id` of the flag table is not in `dq_run_log`.

**Alternatives.**
- Write-Audit-Publish: write Gold to staging tables, run the reconciliation tests on staging, and replace the
  production tables only when every test passes. This is the only option that keeps bad data away from the
  Direct Lake model. Not done now: it doubles the Gold storage, needs a swap step per table (Delta has no atomic
  multi-table swap, so a failure between swaps leaves Gold mixed), and the report has one user, a manual run and
  Delta time travel to roll back (`RESTORE TABLE ... TO VERSION AS OF`). It becomes the right choice when the
  pipeline is scheduled, the report has users who would act on a wrong number before someone restores, or a
  restore is no longer acceptable as the recovery path.
- Fail only in 03 and leave 02 as is: the rule test results would still be ignored, and 03 cannot tell whether 02
  finished.
- Move the rule tests before the flag table is written: needs the rule definitions split out of the DQ cell; the
  `dq_run_log` check gives the same protection with a smaller change.

**Consequences.** A failed run now shows as Failed in Fabric, with the failed tests in the error message.
Gold tables are already overwritten when the reconciliation tests fail; the error message says so, and the
previous version can be restored with time travel. A failed rule test in 02 leaves `silver_petrinex_vol_flagged`
with an unlogged run ID, and 03 refuses it until 02 runs again successfully.
Verified in Fabric on 2026-10-28, with the failure paths run against a temporary copy of the input tables in a
separate Lakehouse (deleted afterwards; production tables were only read):
- Test A (reconciliation failure): rows of one source file (`Vol_2025-07-AB.CSV`, 528,287 rows) deleted from the
  copied `bronze_petrinex_vol`. `03_gold_transform` failed after writing 5 results; only
  `bronze_equals_silver_rows` was FAIL (expected 5,953,674, actual 6,481,961).
- Test B (rule test failure): a copy of `02_silver_transform` with one wrong expected value failed with `clean_row`
  FAIL (6 of 7 PASS) and did not log its run ID in `dq_run_log`. `03_gold_transform` then stopped in its first cell
  ("did not finish"): no new `reconciliation_results` rows, test Gold tables still at the same versions.
- Success path on production: `02_silver_transform` and `03_gold_transform` completed (run `74f1c965`), all 7 rule
  tests and all 5 reconciliation tests passed, Gold moved from versions 6/6/2/2 to 7/7/3/3, and EXCEPT ALL between
  the old and new versions found 0 different rows in both directions for every table (run ID columns excluded).
  Production Gold was still at versions 6/6/2/2 after tests A and B.

---

## DEC-023 Partition gold_fact_production by production_month

**Date:** 2026-10-28

**Context.** Petrinex data arrives as one file per month, and the report filters and compares by month
(MoM and rolling 3-month measures). `gold_fact_production` was written as one unpartitioned table of about
2.9M rows in 10 files (43.9 MB), and every month filter read all of them. 03_gold_transform overwrites the whole
fact table on every run, even when only one month changed.

**Decision.** `03_gold_transform` writes `gold_fact_production` with `partitionBy("production_month")`, after
`repartition("production_month")` so that each month is one file. The rest of the Gold logic is unchanged. The
table is still fully overwritten on every run; partitioning makes a later move to `replaceWhere` (rewrite only the
months that were reloaded) possible without changing the table layout again.

**Alternatives.**
- No partitioning: fast enough at this size, but a month filter reads the whole table, and a reload of one month
  can only be done by rewriting the whole table.
- `partitionBy` without `repartition`: the fact rows come from a filter without a shuffle, so every write task
  would write one small file per month it touches. One file per month keeps the file count predictable for
  Direct Lake.
- Z-order on `production_month`: needs a separate `OPTIMIZE` run, gives data skipping on month filters but not
  `replaceWhere` by month. Better suited to a high-cardinality column such as `well_key`, which must not be a
  partition column.
- Liquid clustering: replaces both partitioning and Z-order and allows changing the clustering columns later.
  Not chosen because the month-based `replaceWhere` reload is the main goal and is simplest with plain
  partitions; worth revisiting if the table grows or queries filter mainly by well or operator.
- V-Order: a Parquet write optimization for Direct Lake reads, independent of partitioning. It is off in this
  workspace (`spark.sql.parquet.vorder.default = false`); turning it on is a separate decision.

**Consequences.** Honest limit: with about 242,000 rows and 3.6 MB per month, each partition is far below the
size usually recommended for Delta partitions (around 1 GB), so the speed-up matters little at this scale.
The main gain is the month-aligned layout and the option to rewrite single months.
Verified in Fabric on 2026-10-28 (run `024b8cef`, `scripts/verify/check_fact_partitioning.py`):
- Layout: `partitionColumns = [production_month]`, 12 files (one per month, 240,280 to 244,620 rows each),
  43.9 MB in total, against 10 files and 43.9 MB before.
- All 7 DQ rule tests and all 5 reconciliation tests passed. Gold moved from versions 7/7/3/3 to 8/8/4/4, and
  EXCEPT ALL between the old and new versions found 0 different rows in both directions for every table
  (`_gold_run_id` and `_silver_run_id` excluded).
- Single-month query (`production_month = 2025-12-01`, sum of volume by product), old unpartitioned version 7
  against new version 8, results identical: files read 10 to 1, bytes read 43,861,493 to 3,652,429, rows scanned
  2,908,544 to 242,040; the new plan has a partition filter, the old one has none (no data skipping on month
  either). Median of 5 timed runs: 1.58 s to 0.29 s. These timings are on a warm session and a small table;
  they show fewer bytes read, not a meaningful saving for the report.
- Direct Lake: the semantic model refresh completed (7 s), and a DAX query of gas volume, producing wells and
  reconciliation status by month returned the same values before and after (largest gas difference 3e-8, from
  floating point summation order). 12 files is far below the Direct Lake file limit per table.
