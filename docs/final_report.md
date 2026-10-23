# GIS track - final report

Branch `feature/gis-layer`, updated 2026-10-23. Detailed log: [`progress.md`](progress.md). Design choices:
[`DECISIONS.md`](../DECISIONS.md) DEC-006 to DEC-020.

Final full run `5d4aabb7` on commit `06985e8` (clean tree): status success in 487 s (convert 119, QA 95,
liability 115, analysis 89, packages 39, changes 14, maps 17); QA 12 PASS / 7 WARN / 4 INFO; all 10 reconciliation
checks PASS. Manifest: `gis/output/full/run_manifest.json`.

## What was built

A config-driven, rerunnable GIS pipeline over the AER ST37 well data (539,890 licences), run with one command
(`python -m gis.run --mode sample|full`), with a run log and a run manifest:

| Step | Module | Result |
|---|---|---|
| Source and stage | `gis.stage` | 4 GDB layers, Alberta boundary, Excel sample and 12 Petrinex files read once into GeoParquet; sample mode = Calgary-region bbox |
| Convert and standardize | `gis.convert` | One GeoPackage in EPSG:3400 plus wells Shapefile and CSV; snake_case fields, typed dates (per-column formats), licence status mapping table |
| Data accuracy checks | `gis.qa`, `gis.qa_checks` | 23 config rules (boundary, datum, DLS grid, referential integrity, trajectories, status, GDB vs Excel, duplicates, missing/accepted values, date range); results in `defect_log` format; licensee scorecard; FAIL rules stop the run unless a failure is on the rule's reviewed allowlist |
| Liability screening | `gis.liability` | Well classes from licence status and Petrinex activity; abandoned-not-reclaimed by licensee and township; inactive candidates; Petrinex match rate with reasons; licensee profile; reconciled with the Fabric pipeline |
| Spatial analysis | `gis.analysis` | Townships, 5 km hexagons, multi-well pads (50 m clustering), wellbore laterals and their trend by spud year |
| Client packages | `gis.package` | By licensee, township list or AOI: GeoPackage with embedded QGIS styles, CSV, data dictionary, README, `.qml` per layer; three example packages |
| Change management | `gis.changes` | Snapshots and a changelog (new/removed licences, status changes, transfers, DLS changes, moves) as CSV and GeoPackage; proven with a synthetic second snapshot |
| Maps | `gis.maps`, `gis.qgis_maps` | QGIS project `gis/alberta_wells.qgz` with 5 print layouts; PNGs in `docs/maps/` |
| Tests | `tests/gis/` | 53 tests: every QA check, GQA-01 allowlist, DLS parser and section numbering, township key, UWI to well key, status mapping, dates, pad clustering, hexagons, packages, change detection, injected-bad-row stop, QGIS log filter |

## Full-province key numbers

- **Inputs:** 539,890 surface holes, 538,629 bottom holes, 658,266 production strings, 533,755 trajectories;
  4,248,675 Petrinex well rows (2025-07 to 2026-06).
- **QA:** 539,846 of 539,890 wells (99.99%) pass every FAIL and WARN rule. Located FAIL/WARN failures: 13 border
  surface holes (allowlisted by licence number with a reason, DEC-015), 9 border bottom holes, 2 DLS township/range mismatches, 2 licences without a
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
- The 13 allowlisted border wells were not checked against an authoritative legal boundary (DEC-015).
- Fabric was not touched; the GIS outputs are not yet loaded into the Lakehouse or the Power BI model.

## Interview talking points by job requirement

No single job posting is stored in the repo. The requirements below are the GIS analyst duties this track was built
against (README "Goal") and the common requirements of the Data Analyst / BI Developer / Junior Data Developer roles the
project targets. Each has the evidence in the repo and a short answer to give in an interview.

### At a glance

| Job requirement | Evidence in the repo | One-line talking point |
|---|---|---|
| Source and evaluate data | README sources table; DEC-006; `gis/stage.py` | "I checked each source before trusting it: the Shapefile silently drops the DLS field, so I used the GDB." |
| Convert and standardize formats / CRS | `gis/convert.py`, `config/gis.yaml`, status mapping CSV | "One config drives GeoPackage, Shapefile and CSV in EPSG:3400, with per-column date formats and a status mapping table." |
| Maintain mapping layers over time | `gis/changes.py`, DEC-018 | "Each ST37 refresh becomes a snapshot and a changelog: new, removed, status, transfer, moved." |
| Check data accuracy (data quality) | `config/gis_qa.yaml` (23 rules), `gis/qa.py`, defect log | "Rules are config, every rule has a test, FAIL stops the run, and known exceptions are an allowlist with reasons." |
| Reconcile across layers and systems | run manifest `reconciliation`; Petrinex vs Fabric | "Township, hexagon and pad counts must equal drilled wells every run; June 2026 matches the Fabric pipeline exactly." |
| Client data packages | `gis/package.py`, `deliverables/` | "One CLI builds a styled, documented package by licensee, township list or AOI." |
| Maps and decision support | `docs/maps/`, `gis/alberta_wells.qgz` | "Maps answer a question, and I fixed what the first render got wrong." |
| Analysis and insight (Data Analyst) | `gis/liability.py`, `gis/analysis.py` | "95,971 abandoned-not-reclaimed wells; horizontal share of new wellbores went from 0.5% in 1980 to 91.5% in 2024." |
| SQL, Python, data modelling (Junior Data Developer) | `notebooks/` (Fabric medallion), `gis/` (pandas/geopandas) | "Bronze/Silver/Gold star schema in Fabric; local GIS pipeline in Python with the same well key." |
| Power BI (BI Developer) | Fabric Gold + Direct Lake model (DEC-005) | "The Gold star schema feeds Power BI; GIS well classes and coordinates are the next dimension columns to load." |
| Automation, testing, reproducibility | `python -m gis.run`, 53 tests, run manifest | "One command, run log and manifest with the git commit; tests use constructed rows, not the real data." |
| Performance | `progress.md` E.1, `handoff.md` timings | "I profiled before optimizing and checked the results did not change." |
| Documentation and communication | README, DECISIONS.md (DEC-001 to DEC-020), handoff | "Every design choice is Context / Decision / Alternatives / Consequences, so a reviewer can disagree with the reason." |

### Sourcing data
- **Evidence:** three public sources with different formats and CRSs (ST37 GDB in EPSG:3400, Petrinex CSV keyed by
  UWI, StatCan Shapefile in EPSG:3347), each documented with date, format, CRS and terms (README).
- **Say:** "I inspected before trusting. The ST37 Shapefile drops the DLS label because of the 10-character DBF field
  limit, so the GDB became the source (DEC-006). I also proved the lat/long datum instead of assuming it: attributes
  match NAD83 (EPSG:4269) to 1e-9 degrees and differ from NAD83(CSRS) by about 1 m."

### Converting and standardizing formats
- **Evidence:** one GeoPackage in EPSG:3400 plus Shapefile and CSV from `config/gis.yaml`; per-column date formats;
  `config/gis_licence_status_map.csv` keeps the raw value and flags unseen ones
  (`test_status_mapping_covers_every_raw_value_and_flags_new_ones`).
- **Say:** "The same date field is YYYYMMDD in one layer and MM/DD/YYYY in another, so formats are set per column in
  config. I staged a 1.7 GB GDB to GeoParquet once and developed on a Calgary sample: seconds per run instead of
  minutes."

### Maintaining mapping layers
- **Evidence:** `gis.changes` snapshot and compare; changelog as CSV and a GeoPackage layer with old-to-new lines; the
  synthetic demo finds all 17 injected changes with no false positives.
- **Say:** "A layer is maintained, not built once. Each ST37 release becomes a snapshot, and the changelog shows new and
  removed licences, status changes, licensee transfers, DLS changes and moved wells. The honest limit: I have one real
  release, so it is proven on a synthetic second snapshot."

### Data accuracy checks
- **Evidence:** 23 config rules with FAIL/WARN/INFO severities; output in the Lakehouse `defect_log` schema; licensee
  scorecard; a meta-test fails if any check has no test; `--inject-bad-row` stops the run with exit code 1.
- **Say:** "539,846 of 539,890 wells pass every FAIL and WARN rule. The interesting part is the exceptions. 13 border
  wells lie 110-571 m outside the StatCan boundary, but their lat/long and DLS labels agree, so it is boundary data vs
  survey. At first I tolerated them with a count of 13, then I saw the flaw: any 13 failures would pass, and my own
  injected bad row passed that rule as a warning. I replaced the count with an allowlist of the 13 licence numbers,
  each with a written reason. Any other well outside Alberta now fails the run, and a test proves it."
- **Other findings that changed the design:** `SHAPE_Length` is 2D, so 341,799 "zero-length" trajectories are vertical
  wells, not empty geometry; a 2030 status date became a new rule (GQA-23); a 74-licence pad that distorted the
  inactive ranking became a review class.

### Reconciliation
- **Evidence:** `run_manifest.json` reconciliation rows (township, hexagon and pad well counts equal drilled wells;
  hexagon oil equals well oil); Petrinex rebuilt locally and compared with the Fabric pipeline.
- **Say:** "Every run checks that derived layers add back to the source. The local Petrinex rebuild matches the Fabric
  pipeline exactly for June 2026: 161,098 well keys, 135,154 producing. 99.994% of producing wells match an ST37
  licence, and the unmatched ones carry a reason code."

### Client data packages
- **Evidence:** `python -m gis.package --licensee | --townships | --aoi`; GeoPackage with embedded QGIS styles (verified
  in PyQGIS), CSV, generated data dictionary, README with as-of date, CRS, QA summary and known issues.
- **Say:** "A client opens the GeoPackage and it is already styled. Every field is described, and the README says what
  the data cannot do, for example that screening classes are not AER criteria."

### Maps and decision support
- **Evidence:** 5 print layouts in `gis/alberta_wells.qgz`; PNGs in `docs/maps/` with title, legend, scale bar, north
  arrow, source and CRS.
- **Say:** "The first class map plotted 540,000 points, and whichever class was drawn last hid the others: abandoned
  wells covered 148,479 active ones. I switched to the most common class per 5 km hexagon. Checking the output is part
  of the job, not just producing it."

### Analysis and insight (Data Analyst)
- **Evidence:** liability classes, top inactive licensees, abandoned-not-reclaimed by township, pads, laterals.
- **Say:** "95,971 wells are abandoned but not reclaimed, 25,661 of them held by Canadian Natural. Some licensees have
  an inactive ratio of 1.00 (AlphaBow, Sequoia). 42,537 multi-well pads hold 29.5% of drilled wells, and the horizontal
  share of new wellbores rose from 0.5% in 1980 to 51.7% in 2012 and 91.5% in 2024. These are screening definitions I
  wrote down, not regulatory findings, and every output says so."

### SQL, Python and data modelling (Junior Data Developer)
- **Evidence:** Fabric notebooks `01_bronze_transform` to `03_gold_dims` (medallion, star schema, DEC-005); GIS pipeline
  in pandas/geopandas; the same UWI-to-well-key rule in both (`test_uwi_to_well_key_matches_silver_rule`).
- **Say:** "The Fabric track is Bronze/Silver/Gold with a star schema. The GIS track reuses the Silver well-key rule,
  and a test proves the two agree, so GIS outputs can join to Gold without a mapping table."

### Power BI (BI Developer)
- **Evidence:** Gold star schema and Direct Lake semantic model (TMDL in the repo); MoM and rolling 3-month measures
  because there are only 12 months of data.
- **Say:** "With 12 months I used MoM and rolling 3-month measures, not YoY. The next step is to load the GIS well
  classes and coordinates into `gold_dim_well` so the report can map them."

### Automation, testing and reproducibility
- **Evidence:** `python -m gis.run --mode sample|full`; `run_log.txt` and `run_manifest.json` (git commit and dirty
  flag, per-step status and runtime, row counts, QA summary, reconciliation); 53 tests on constructed rows; a
  QGIS log filter that hides only one exact, reviewed GDAL message (DEC-020).
- **Say:** "Any run can be traced to a commit. Tests use small constructed rows, so they run in seconds without the
  1.7 GB source. I filter log noise by exact message, never by prefix, so a new error cannot hide."

### Performance
- **Say:** "I profiled before optimizing and checked the results were identical after: Petrinex month parsing 50 s to
  1 s (parse the 12 distinct months once), pad statistics 464 s to about 100 s (numpy-backed groupby instead of a
  Python fallback on Arrow types), QA flags 213 s to about 102-137 s. The full province now runs in about 8
  minutes."

### Documentation and communication
- **Evidence:** README (goal, architecture diagram, sources, how to run, QA approach, findings, maps, known
  limitations), DECISIONS.md DEC-001 to DEC-020, `progress.md`, `handoff.md`.
- **Say:** "I write down what I decided and what I rejected. The known limitations are in the README, not hidden: no
  ATS layer, undecoded status codes, one ST37 snapshot, screening classes that are project definitions."
