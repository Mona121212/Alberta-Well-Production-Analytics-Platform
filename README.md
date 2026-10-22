# Alberta-Well-Production-Analytics-Platform
A Microsoft Fabric data platform for ingesting, validating, transforming, and analyzing Alberta oil and gas well production data.

## GIS track (local Python)

Converts AER ST37 well data to a standardized GeoPackage in NAD83 / Alberta 10-TM (EPSG:3400), runs spatial QA,
and builds client data packages. Configuration: `config/gis.yaml`. Outputs: `gis/output/<mode>/` (not in Git).

```bash
pip install -r requirements.txt
python -m gis.stage   --mode sample   # GDB + boundary -> GeoParquet (once per mode)
python -m gis.convert --mode sample   # -> alberta_wells.gpkg, wells_shp/alberta_wells.shp, alberta_wells.csv
```

`sample` uses a Calgary-region bbox for fast development; use `--mode full` for the whole province.
Inputs (not in Git): `data/raw/aer_st37/ST_37_Wells.gdb` and the Statistics Canada 2021 province boundary
`data/raw/boundaries/lpr_000b21a_e.shp`.
