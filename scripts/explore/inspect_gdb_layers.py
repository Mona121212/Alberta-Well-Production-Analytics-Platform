"""List every layer in a File Geodatabase with geometry type, CRS, row count, bounds and fields.

Usage:
    python scripts/explore/inspect_gdb_layers.py [path/to/file.gdb]
"""
import sys

import pyogrio
import pyproj

DEFAULT_GDB = "data/raw/aer_st37/ST_37_Wells.gdb"


def main(gdb):
    for name, geom_type in pyogrio.list_layers(gdb):
        info = pyogrio.read_info(gdb, layer=name, force_feature_count=True)
        crs_name, epsg = None, None
        if info["crs"]:
            crs = pyproj.CRS.from_user_input(info["crs"])
            crs_name, epsg = crs.name, crs.to_epsg()
        print(f"\n=== {name}")
        print(f"    geometry: {geom_type} | rows: {info['features']:,} | CRS: {crs_name} (EPSG:{epsg})")
        print(f"    bounds:   {info.get('total_bounds')}")
        print(f"    fields:   {list(zip(info['fields'], info['dtypes']))}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GDB)
