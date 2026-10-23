# Well data package: Cenovus Energy Inc.

Selection: licensee = **Cenovus Energy Inc.**. Generated 2026-10-22 by `python -m gis.package` (Alberta Well Production Analytics Platform, GIS track).

## As-of dates

- AER ST37 wells: latest licence status date in the data is 2026-09-02 (future-dated records excluded; see QA rule GQA-23).
- Petrinex volumes: 2025-07 to 2026-06 (as-of month 2026-06). Production columns cover these 12 months.

## Coordinate reference system

NAD83 / Alberta 10-TM (Forest), EPSG:3400, metres. Latitude/longitude attributes are NAD83 (EPSG:4269).

## Contents

| Layer | Geometry | Rows |
|---|---|---:|
| `wells` | point | 42,029 |
| `pads` | point | 29,737 |
| `qa_flags` | table | 34,129 |
| `package_extent` | polygon | 1 |
| `alberta_boundary` | polygon | 1 |
| `wells.csv` | none | 42,029 |

Open `data.gpkg` in QGIS: styles are embedded and load automatically. The `.qml` files hold the same styles (Layer Properties > Style > Load Style). Field definitions: `data_dictionary.md`.

## Wells by screening class

| Class | Wells |
|---|---:|
| reclaimed | 16,631 |
| abandoned | 6,712 |
| active_reporting | 5,732 |
| suspended | 5,277 |
| cancelled | 4,926 |
| active_no_activity_12m | 1,840 |
| active_recently_spudded | 285 |
| active_not_spudded | 277 |
| active_no_activity_6m | 241 |
| review_multi_licence_pad_never_reported | 108 |

Inactive candidates: 2,081 (6 months), 1,840 (12 months).

## Data quality summary

42,027 of 42,029 wells (100.00%) pass every FAIL and WARN rule. Records flagged for this package's licences:

| Rule | Name | Severity | Records |
|---|---|---|---:|
| GQA-06 | dls_section_mismatch | INFO | 48 |
| GQA-11 | trajectory_end_far_from_bottom_hole | WARN | 2 |
| GQA-13 | status_mode_text_instead_of_code | INFO | 8,220 |
| GQA-15 | gdb_vs_excel_difference | INFO | 382 |
| GQA-22 | vertical_only_trajectory | INFO | 25,477 |

Rules and severities: `config/gis_qa.yaml` in the project repository. INFO rules describe known source characteristics and do not count against a well.

## Sources

| Source | Content | Format |
|---|---|---|
| Alberta Energy Regulator, ST37 List of Wells in Alberta | Wells, licences, licensees, status, locations | File Geodatabase |
| Petrinex public data, Alberta volumetrics | Monthly well production, injection and disposal volumes | CSV |
| Statistics Canada, 2021 Census boundary files (provinces) | Alberta boundary | Shapefile |

Use of the source data is subject to each provider's terms.

## Known issues

- Well classes are screening definitions for this project, not AER regulatory classifications (DEC-012).
- Inactive candidates use Petrinex production, injection and disposal volumes in the loaded 12 months only; wells that never report to Petrinex by design can appear as candidates (DEC-012, DEC-014).
- Status_Mode/Status_Fluid in ST37 production strings mix numeric codes and text and are not decoded (DEC-011).
- The DLS check uses an approximate township grid; section-level differences under one section are not reliable (DEC-010).
- 13 surface holes along the 110 W and 120 W borders lie 110-571 m outside the Statistics Canada boundary (DEC-015).
- Pads are surface holes within 50 m of each other (single linkage); township polygons are convex hulls of wells, not surveyed townships (DEC-016).
