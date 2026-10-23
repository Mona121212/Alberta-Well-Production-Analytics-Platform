# GIS track - final report

Branch `feature/gis-layer`, 2026-10-22. Detailed log: [`progress.md`](progress.md). Design choices:
[`DECISIONS.md`](../DECISIONS.md) DEC-006 to DEC-019.

## What was built

A config-driven, rerunnable GIS pipeline over the AER ST37 well data (539,890 licences), run with one command
(`python -m gis.run --mode sample|full`), with a run log and a run manifest:

| Step | Module | Result |
|---|---|---|
| Source and stage | `gis.stage` | 4 GDB layers, Alberta boundary, Excel sample and 12 Petrinex files read once into GeoParquet; sample mode = Calgary-region bbox |
| Convert and standardize | `gis.convert` | One GeoPackage in EPSG:3400 plus wells Shapefile and CSV; snake_case fields, typed dates (per-column formats), licence status mapping table |
| Data accuracy checks | `gis.qa`, `gis.qa_checks` | 23 config rules (boundary, datum, DLS grid, referential integrity, trajectories, status, GDB vs Excel, duplicates, missing/accepted values, date range); results in `defect_log` format; licensee scorecard; FAIL rules stop the run |
| Liability screening | `gis.liability` | Well classes from licence status and Petrinex activity; abandoned-not-reclaimed by licensee and township; inactive candidates; Petrinex match rate with reasons; licensee profile; reconciled with the Fabric pipeline |
| Spatial analysis | `gis.analysis` | Townships, 5 km hexagons, multi-well pads (50 m clustering), wellbore laterals and their trend by spud year |
| Client packages | `gis.package` | By licensee, township list or AOI: GeoPackage with embedded QGIS styles, CSV, data dictionary, README, `.qml` per layer; three example packages |
| Change management | `gis.changes` | Snapshots and a changelog (new/removed licences, status changes, transfers, DLS changes, moves) as CSV and GeoPackage; proven with a synthetic second snapshot |
| Maps | `gis.maps`, `gis.qgis_maps` | QGIS project `gis/alberta_wells.qgz` with 5 print layouts; PNGs in `docs/maps/` |
| Tests | `tests/gis/` | 47 tests: every QA check, DLS parser and section numbering, UWI to well key, status mapping, dates, pad clustering, hexagons, packages, change detection, injected-bad-row stop |

## Full-province key numbers

- **Inputs:** 539,890 surface holes, 538,629 bottom holes, 658,266 production strings, 533,755 trajectories;
  4,248,675 Petrinex well rows (2025-07 to 2026-06).
- **QA:** 539,846 of 539,890 wells (99.99%) pass every FAIL and WARN rule. Located FAIL/WARN failures: 13 border
  surface holes (tolerated, DEC-015), 9 border bottom holes, 2 DLS township/range mismatches, 2 licences without a
  wellbore, 28 trajectories ending far from the bottom hole, 8 producing modes on closed licences, 1 future status date.
  INFO: 1,041 approximate section mismatches, 162,680 text-coded Status_Mode values, 341,799 vertical-only trajectories.
- **Liability classes:** reclaimed 150,276; active reporting 148,479; abandoned not reclaimed 95,971; suspended 63,959;
  cancelled 54,430; no activity 12 months 17,445; no activity 6 months 3,268; not spudded 3,999; recently spudded 1,642;
  pad review 421.
- **Petrinex match:** 144,255 producing wells, 99.994% matched to a surface licence; June 2026 reconciles exactly with
  the Fabric pipeline (161,098 well keys, 135,154 producing).
- **Top inactive licensees** (no activity 12 months + suspended): Canadian Natural 20,269; Cenovus 7,117;
  Long Run 2,735; Imperial 2,137; InPlay 1,965; AlphaBow 1,881 (inactive ratio 1.00); Sequoia 1,657 (1.00).
- **Spatial:** 481,347 drilled wells; 5,756 townships; 21,994 occupied 5 km hexagons (densest 1,455 wells);
  382,090 pads, 42,537 multi-well pads holding 29.5% of wells; horizontal share of new wellbores 0.5% (1980),
  51.7% (2012), 91.5% (2024).
- **Packages:** Canadian Natural 119,787 wells / 82,484 pads; Cenovus 42,029 / 29,737; Suncor 16,865 / 11,232.

## Blocked or not done

Nothing was blocked (`progress.md` has no Blocked entries). Known gaps, by design or for lack of a source:

- No ATS township polygons (licensed AltaLIS product): DLS checks and township polygons are approximations (DEC-010, DEC-016).
- No official AER status code table: numeric `Status_Mode` / `Status_Fluid` codes are not decoded (DEC-011).
- Only one ST37 snapshot: change detection is demonstrated on a synthetic second snapshot (DEC-018).
- The border tolerance (13 cases) was not checked against an authoritative legal boundary (DEC-015).
- Fabric was not touched; the GIS outputs are not yet loaded into the Lakehouse or the Power BI model.

## Interview talking points by job requirement

**Sourcing data**
- Pulled three public sources with different formats and CRSs (GDB in EPSG:3400, Petrinex CSV keyed by UWI,
  StatCan Shapefile in EPSG:3347) and documented each in a sources table with date, format, CRS and terms.
- Inspected before trusting: the Shapefile version silently drops the DLS label field (10-character DBF limit), so
  the GDB became the source (DEC-006).
- Proved the lat/long datum instead of assuming it: attributes match EPSG:4269 to 1e-9 degrees and differ from
  NAD83(CSRS) by about 1 m.

**Converting and standardizing formats**
- One GeoPackage in EPSG:3400 plus Shapefile and CSV exports from one config file; Shapefile field names mapped to the
  10-character limit explicitly.
- Per-column date formats (the same field is YYYYMMDD in one layer and MM/DD/YYYY in another) and a licence status
  mapping table that keeps the raw value and catches a case variant.
- Staged a 1.7 GB GDB to GeoParquet once and developed on a bbox sample: runs in seconds, full province in minutes.

**Maintaining mapping layers**
- Snapshot and changelog workflow for ST37 refreshes: new and removed licences, status changes, licensee transfers,
  DLS label changes and moved locations, as CSV and a GeoPackage layer with old-to-new lines.
- Derived layers kept in sync by one command and reconciled on every run (township, hexagon and pad counts equal the
  drilled-well count; hexagon volumes equal well volumes).

**Client data packages**
- One CLI for a licensee, a township list or an AOI polygon; packages open styled in QGIS because the styles are
  embedded in the GeoPackage (verified with PyQGIS), and every field is described in a generated data dictionary.
- Each package README carries as-of dates, CRS, sources, a QA summary and known issues, so a client sees the limits.

**Mapping support**
- PyQGIS print layouts with title, legend, scale bar, north arrow and source/CRS note, saved in a QGIS project so they
  stay editable.
- Chose hexagons over 540,000 points for the class map after the first render showed one class hiding another:
  an example of checking the output, not just producing it.

**Data accuracy checks**
- 23 config-driven rules with FAIL/WARN/INFO severities; FAIL stops the pipeline, shown with an injected bad row.
- Findings that changed the design: 13 border wells explained by the boundary data (tolerated with a count, not a
  wider buffer); 2D `SHAPE_Length` misread as empty trajectories; a 2030 status date that became a new rule; a
  74-licence pad that distorted the inactive ranking and became a review class.
- Reconciled the local Petrinex rebuild with the Fabric pipeline's own numbers (exact match for June 2026).

**Engineering habits**
- Profiled and fixed slow steps instead of waiting: Period parsing (50 s to 1 s), pad statistics (464 s to 127 s via
  vectorized groupby on numpy-backed columns), QA flags (213 s to 102 s).
- Every design choice recorded as Context / Decision / Alternatives / Consequences.
