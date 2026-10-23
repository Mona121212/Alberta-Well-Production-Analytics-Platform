# Alberta Well Production Analytics Platform

Two tracks built on the same public data:

- **Fabric pipeline** (`notebooks/`): Petrinex monthly production and AER well data through a Bronze / Silver / Gold
  medallion architecture in a Microsoft Fabric Lakehouse, feeding a Power BI semantic model.
- **GIS track** (`gis/`, this README): AER ST37 well locations converted, standardized, quality-checked, analysed and
  delivered as client data packages and maps. Local Python (geopandas / pyogrio) plus PyQGIS for cartography.

![Well screening classes](docs/maps/liability_classes.png)

## Goal

Show the day-to-day work of a GIS analyst on a real, messy provincial dataset (539,890 well licences):
sourcing data, converting and standardizing formats, maintaining mapping layers, checking data accuracy with
repeatable rules, producing client data packages, and supporting decisions with maps.

## Architecture

```mermaid
flowchart LR
    subgraph Sources
        GDB[AER ST37<br/>File Geodatabase<br/>4 layers, EPSG:3400]
        XLS[ST37 Excel extract]
        PTX[Petrinex volumetrics<br/>12 monthly CSVs]
        BND[StatCan 2021<br/>province boundary]
    end
    subgraph "gis.stage (once per mode)"
        PQ[(GeoParquet<br/>sample bbox or full<br/>+ full-province key tables)]
    end
    GDB --> PQ
    XLS --> PQ
    PTX --> PQ
    BND --> PQ
    PQ --> CONV[gis.convert<br/>rename, trim, dates,<br/>status mapping, EPSG:3400]
    CONV --> GPKG[(alberta_wells.gpkg<br/>wells SHP + CSV)]
    GPKG --> QA{gis.qa<br/>23 config rules}
    QA -- "FAIL > max_failures" --> STOP[run stops, exit 1]
    QA --> LIAB[gis.liability<br/>well classes, Petrinex match,<br/>licensee profile]
    LIAB --> ANA[gis.analysis<br/>townships, 5 km hexagons,<br/>pads, laterals]
    ANA --> PKG[gis.package<br/>deliverables/&lt;client&gt;/]
    ANA --> MAPS[gis.maps + PyQGIS<br/>alberta_wells.qgz, docs/maps]
    GPKG --> CHG[gis.changes<br/>snapshot + changelog]
    RUN[[python -m gis.run<br/>run log + manifest]] -.-> CONV
```

Every step reads its settings from `config/` (layer and field names, date formats, the licence status mapping table,
QA rules and severities, liability definitions, analysis units, styles, package contents). Sample and full runs write
to separate folders (`gis/output/sample/`, `gis/output/full/`).

## Data sources

| Source | Content used | Date / version | Format | CRS | Licence / terms |
|---|---|---|---|---|---|
| Alberta Energy Regulator, ST37 List of Wells in Alberta | Surface holes, bottom holes, production strings, well trajectories (539,890 licences) | Downloaded 2026-09-29; latest status date 2026-09-02 | File Geodatabase (also Shapefile and Excel) | NAD83 / Alberta 10-TM (Forest), EPSG:3400; lat/long NAD83 (EPSG:4269) | AER public data, subject to AER terms of use |
| Petrinex public data, Alberta volumetrics | Monthly well volumes by activity and product (4,248,675 well rows) | 2025-07 to 2026-06 | CSV (`Vol_YYYY-MM-AB.CSV`) | none (UWI / DLS keys) | Petrinex public data terms |
| Statistics Canada, 2021 Census cartographic boundary file, provinces | Alberta boundary (PRUID 48) | 2021 census, file dated 2021-12 | Shapefile | NAD83 / Statistics Canada Lambert, EPSG:3347 | Statistics Canada Open Licence |

Raw files live in `data/raw/` and are not in Git. The GDB is read once into GeoParquet (`gis.stage`); every later
step reads Parquet or the standardized GeoPackage.

## How to run

```bash
pip install -r requirements.txt
python -m gis.run --mode sample          # whole track on a Calgary-region sample (about 1-2 minutes)
python -m gis.run --mode full            # whole province (about 10-12 minutes)
python -m pytest tests/gis               # 47 tests with constructed rows
```

`gis.run` writes `gis/output/<mode>/run_log.txt` and `run_manifest.json` (git commit, per-step status and runtime, row
counts per layer, QA summary, reconciliation results). Single steps: `python -m gis.<step> --mode sample`
for `stage`, `convert`, `qa`, `liability`, `analysis`, `maps`; plus:

```bash
python -m gis.package --mode full --licensee "Suncor Energy Inc."   # or --townships 076-06W4,095-06W4 or --aoi aoi.gpkg
python -m gis.package --mode full --top-licensees 3
python -m gis.changes snapshot --mode full --date 2026-10-22       # after each ST37 refresh
python -m gis.changes compare --old gis/output/snapshots/full/wells_2026-10-22.parquet --new gis/output/snapshots/full/wells_<next>.parquet
python -m gis.changes demo --mode sample                           # synthetic second snapshot with known changes
```

Maps need a local QGIS (path in `config/gis_maps.yaml`); without it `gis.run` skips the maps step with a warning.

## Outputs

| Output | Content |
|---|---|
| `gis/output/<mode>/alberta_wells.gpkg` | Standardized layers in EPSG:3400: `wells_surface`, `bottom_holes`, `production_strings`, `well_trajectories`, `alberta_boundary`; wells also as Shapefile and CSV |
| `.../qa/` | `qa_defect_log.csv` (same columns as the Lakehouse `defect_log`), rule summary, all failures, 5 examples per rule, licensee scorecard, `qa_results.gpkg` (one layer per rule + `wells_qa`) |
| `.../liability/` | `liability.gpkg` (`wells_liability`, `abandoned_not_reclaimed`, `inactive_candidates`, `abandoned_by_township`) and CSV summaries |
| `.../analysis/` | `analysis.gpkg` (`townships`, `hexagons_5km`, `pads`, `wellbore_laterals`) and CSV summaries |
| `deliverables/<client>/` | Client package: `data.gpkg` with embedded QGIS styles, `wells.csv`, `data_dictionary.md`, `README.md`, `.qml` per layer |
| `gis/alberta_wells.qgz`, `docs/maps/` | QGIS project with print layouts, and the exported maps |

## Data quality approach

Rules are data, not code: each entry in `config/gis_qa.yaml` names a generic check, the layer, a severity and
parameters. FAIL rules stop the run when failures exceed `max_failures` (after all outputs are written), WARN rules
count against a well in the scorecard, INFO rules document known source characteristics. Results use the
`defect_log` columns, and every rule has a constructed-row test (a meta-test fails if a check has none).

Inject a bad row and the run stops:

```text
$ python -m gis.run --mode sample --inject-bad-row
WARNING gis.qa: Injected one bad well (INJECTED) into wells_surface for the stop demo
INFO gis.qa: GQA-01 surface_hole_outside_alberta                  WARN  1 of 3,300 failed
INFO gis.qa: GQA-03 surface_latlong_mismatch                      FAIL  1 of 3,300 failed
INFO gis.run: Step qa         stopped      7.3 s
INFO gis.run: Run c1f71f4c stopped in 7 s; manifest: gis/output/sample/run_manifest.json
$ echo $?
1
```

The injected well keeps its lat/long but its geometry is moved 50 km outside Alberta; GQA-03 (geometry vs NAD83
lat/long, FAIL, `max_failures: 0`) stops the run, and liability, analysis, packages and maps never run on bad data.

## Key findings (full province)

**Data quality** (23 rules, 539,890 wells): 539,846 wells (99.99%) pass every FAIL and WARN rule.
- 13 surface holes along the 110 W / 120 W borders lie 110-571 m outside the StatCan boundary while their lat/long and
  DLS labels agree; tolerated as a documented boundary-vs-survey difference (DEC-015). Any 14th case stops the run.
- Geometry matches the NAD83 lat/long attributes for all 1.08 million surface and bottom holes (GQA-03/04).
- DLS labels: only 2 surface holes fall more than a quarter township/range outside their labelled cell.
- 28 trajectories end more than 50 m from their bottom hole (up to 4.5 km); 8 production strings report a producing
  mode on an abandoned or reclaimed licence; 1 licence has a status date in 2030.
- `SHAPE_Length` is 2D: 341,799 "zero-length" trajectories are vertical wells, not empty geometry (DEC-011).
- `Status_Mode` mixes 495,586 numeric codes with 162,680 text values, and the Excel extract decodes them inconsistently.

**Liability screening** (project definitions, not AER criteria, DEC-012):
- 95,971 abandoned-not-reclaimed wells; most held by Canadian Natural (25,661), Cenovus (6,712), Suncor (2,192).
- 17,445 active licences with no production, injection or disposal in 12 months, plus 63,959 suspended; 421 licences on
  large never-reported pads are kept apart for review (DEC-014).
- Petrinex-to-ST37 match: 99.994% of 144,255 producing wells; June 2026 reconciles exactly with the Fabric pipeline
  (161,098 well keys, 135,154 producing).

**Spatial patterns**:
- 481,347 drilled wells on 382,090 surface pads; 42,537 multi-well pads hold 29.5% of wells; the largest has 137 licences.
- Horizontal wellbores (surface-to-bottom-hole offset >= 500 m): 0.5% of 1980 spuds, 51.7% in 2012, 91.5% in 2024; the
  median horizontal offset grew from 646 m (1960) to about 1.9 km.

## Maps

| | |
|---|---|
| ![Hexagon well density](docs/maps/hexagon_well_density.png) | ![Abandoned not reclaimed by township](docs/maps/abandoned_not_reclaimed_by_township.png) |
| ![QA failures](docs/maps/qa_failures.png) | ![Client package overview](docs/maps/client_package_overview.png) |

## Limitations

- Screening classes and thresholds are project working definitions; no AER regulatory threshold is claimed.
- No ATS township layer: the DLS check uses a calibrated approximate grid (DEC-010) and township polygons are convex
  hulls of wells (DEC-016).
- One ST37 snapshot: change detection is proven on a synthetic second snapshot (DEC-018).
- Petrinex covers 12 months; production volumes are not converted between units (gas e3m3, liquids m3).
- Status codes in production strings are not decoded (no official code table in the repo).
- Maps need a local QGIS install; packages and all analysis do not.

## Repository guide

- `gis/` - pipeline modules (`stage`, `convert`, `qa`, `qa_checks`, `liability`, `analysis`, `package`, `changes`,
  `maps`, `qgis_maps`, `run`)
- `config/` - every setting, rule, mapping table, field dictionary and style
- `tests/gis/` - rule and module tests
- `docs/` - `progress.md`, `final_report.md`, `st37_gis_exploration.md`, `defect_log.md`, maps
- `DECISIONS.md` - Context / Decision / Alternatives / Consequences for every design choice (DEC-001 to DEC-019)
