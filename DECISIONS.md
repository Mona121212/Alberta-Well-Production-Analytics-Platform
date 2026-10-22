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
