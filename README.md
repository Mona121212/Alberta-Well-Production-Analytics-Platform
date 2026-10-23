# Alberta-Well-Production-Analytics-Platform
A Microsoft Fabric data platform for ingesting, validating, transforming, and analyzing Alberta oil and gas well production data.

## GIS track (local Python)

Converts AER ST37 well data to a standardized GeoPackage in NAD83 / Alberta 10-TM (EPSG:3400), runs spatial QA,
and builds client data packages. Configuration: `config/gis.yaml`. Outputs: `gis/output/<mode>/` (not in Git).

```bash
pip install -r requirements.txt
python -m gis.stage   --mode sample   # GDB + boundary -> GeoParquet (once per mode)
python -m gis.convert --mode sample   # -> alberta_wells.gpkg, wells_shp/alberta_wells.shp, alberta_wells.csv
python -m gis.qa      --mode sample   # -> qa/ (defect log, rule summary, examples, scorecard, qa_results.gpkg)
python -m gis.liability --mode sample # -> liability/ (abandoned, inactive candidates, licensee profile)
python -m gis.analysis --mode sample  # -> analysis/ (townships, 5 km hexagons, pads, laterals)
python -m gis.pipeline --mode sample  # all steps; a FAIL QA rule or failed reconciliation stops it (exit 1)
python -m pytest tests/gis            # rule tests with constructed rows
```

QA rules (`config/gis_qa.yaml`, 22 rules): location inside Alberta (100 m tolerance), geometry vs NAD83 lat/long,
DLS label vs location (approximate township grid, DECISIONS.md DEC-010), referential integrity between surface holes,
bottom holes and production strings, trajectory start/end vs surface/bottom hole, status consistency, GDB vs Excel,
duplicates, missing and accepted values. Results use the `defect_log` columns; a scorecard gives the share of wells
passing all FAIL/WARN rules by licensee.

`sample` uses a Calgary-region bbox for fast development; use `--mode full` for the whole province.
Inputs (not in Git): `data/raw/aer_st37/ST_37_Wells.gdb` and the Statistics Canada 2021 province boundary
`data/raw/boundaries/lpr_000b21a_e.shp`.

Liability screening (`config/gis_liability.yaml`, DECISIONS.md DEC-012/013): abandoned-but-not-reclaimed wells by
licensee and township, active licences with no Petrinex activity in the last 6/12 months, Petrinex -> ST37 match
rate with unmatched reasons, and a licensee profile (active / inactive / abandoned / reclaimed). These are project
working definitions for screening, not AER regulatory criteria.

Client packages and change detection:

```bash
python -m gis.package --mode full --licensee "Suncor Energy Inc."      # or --townships 076-06W4,095-06W4 / --aoi aoi.gpkg
python -m gis.package --mode full --top-licensees 3                     # examples in deliverables/
python -m gis.changes snapshot --mode full --date 2026-10-22            # after each ST37 refresh
python -m gis.changes compare --old gis/output/snapshots/full/wells_2026-10-22.parquet                               --new gis/output/snapshots/full/wells_<next-date>.parquet
python -m gis.changes demo --mode sample                                # synthetic second snapshot with known changes
```
