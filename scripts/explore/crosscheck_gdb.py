"""Cross-layer checks on the AER ST37 geodatabase.

- Production_String: rows where Prod_String_UWI differs from Well_UWI, UWI prefix/event mix
- Key overlap between Production_String, Bottom_Hole and Well_Geometry (by UWI)
- Licence match from Production_String to Surface_Hole
- Zero-length trajectories in Well_Geometry
- Whether BH_Latitude/BH_Longitude equal the layer geometry reprojected to NAD83 (EPSG:4269)
- Raw date string formats

Usage:
    python scripts/explore/crosscheck_gdb.py [path/to/file.gdb]
"""
import sys

import numpy as np
import pandas as pd
import pyogrio
from pyproj import Transformer

DEFAULT_GDB = "data/raw/aer_st37/ST_37_Wells.gdb"
LAYER_TEMPLATE = "ST37_{}_NAD83_10TM_AEP_Forest"
SOURCE_EPSG, LATLON_EPSG = 3400, 4269
SAMPLE_SIZE = 20000


def read(gdb, layer, columns, geometry=False):
    return pyogrio.read_dataframe(gdb, layer=LAYER_TEMPLATE.format(layer),
                                  columns=columns, read_geometry=geometry)


def main(gdb):
    pd.set_option("display.width", 200)

    ps = read(gdb, "Production_String", ["Prod_String_UWI", "Well_UWI", "Well_Licence_Number",
                                         "Status_Fluid", "Status_Mode", "Status_Type"])
    diff = ps[ps.Prod_String_UWI != ps.Well_UWI]
    print("Production_String rows where Prod_String_UWI != Well_UWI:", f"{len(diff):,}")
    print(diff.head(6).to_string())
    print("Location exception prefixes:", ps.Well_UWI.str[:2].value_counts().head(10).to_dict())
    print("Event sequence values:", ps.Well_UWI.str[-1:].value_counts().head(10).to_dict())
    print("Status_Fluid top values:", ps.Status_Fluid.value_counts().head(8).to_dict())

    bh = read(gdb, "Bottom_Hole", ["Well_UWI", "BH_Latitude", "BH_Longitude",
                                   "Final_Drill_Date", "Licence_Status_Date"], geometry=True)
    geom = read(gdb, "Well_Geometry", ["UWI_Label", "SHAPE_Length", "Well_Geometry_Source"])
    sh = read(gdb, "Surface_Hole", ["Well_Licence_Number", "SH_Actual_Latitude", "SH_Actual_Longitude"])

    p, b, g = set(ps.Well_UWI), set(bh.Well_UWI), set(geom.UWI_Label)
    print(f"\nKey overlap: PS={len(p):,} BH={len(b):,} GEOM={len(g):,}")
    print(f"  PS&BH={len(p & b):,} PS-BH={len(p - b):,} BH-PS={len(b - p):,}")
    print(f"  BH&GEOM={len(b & g):,} BH-GEOM={len(b - g):,} GEOM-BH={len(g - b):,}")
    licence_match = ps.Well_Licence_Number.str.strip().isin(set(sh.Well_Licence_Number)).mean()
    print(f"Production_String licence found in Surface_Hole: {licence_match:.4%}")

    zero = geom[geom.SHAPE_Length == 0]
    print(f"\nWell_Geometry SHAPE_Length == 0: {len(zero):,} by source {zero.Well_Geometry_Source.value_counts().to_dict()}")

    print("\nBH lat/lon nulls:", bh.BH_Latitude.isna().sum(), bh.BH_Longitude.isna().sum(),
          "| SH lat/lon nulls:", sh.SH_Actual_Latitude.isna().sum(), sh.SH_Actual_Longitude.isna().sum())
    print("BH latitude range:", bh.BH_Latitude.min(), bh.BH_Latitude.max(),
          "| longitude range:", bh.BH_Longitude.min(), bh.BH_Longitude.max())

    to_latlon = Transformer.from_crs(SOURCE_EPSG, LATLON_EPSG, always_xy=True)
    s = bh.sample(min(SAMPLE_SIZE, len(bh)), random_state=1)
    lon, lat = to_latlon.transform(s.geometry.x.values, s.geometry.y.values)
    dist = np.hypot(lon - s.BH_Longitude.values, lat - s.BH_Latitude.values)
    print(f"EPSG:{SOURCE_EPSG} -> EPSG:{LATLON_EPSG} vs BH_Latitude/BH_Longitude ({len(s):,} rows): "
          f"median diff {np.median(dist):.2e} deg, max {dist.max():.2e} deg, rows > 1e-4 deg: {(dist > 1e-4).sum()}")

    print("\nRaw date samples: Final_Drill_Date", bh.Final_Drill_Date.dropna().sample(5, random_state=2).tolist(),
          "| Licence_Status_Date", bh.Licence_Status_Date.dropna().sample(3, random_state=2).tolist())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GDB)
