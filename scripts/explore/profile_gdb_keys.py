"""Profile key columns of the AER ST37 geodatabase layers and print sample rows.

For each key: nulls, blanks, distinct values, duplicated rows, padded values, and how many
values match the UWI pattern used by notebooks/02_silver_transform.py.

Usage:
    python scripts/explore/profile_gdb_keys.py [path/to/file.gdb]
"""
import sys

import pandas as pd
import pyogrio

DEFAULT_GDB = "data/raw/aer_st37/ST_37_Wells.gdb"

# Same pattern as UWI_PATTERN in notebooks/02_silver_transform.py
UWI_PATTERN = r"^(\w{2})/(\d{2})-(\d{2})-(\d{3})-(\d{2})W(\d)/(\d+)$"

LAYER_KEYS = {
    "ST37_Production_String_NAD83_10TM_AEP_Forest": ["Prod_String_UWI", "Well_UWI", "Well_Licence_Number"],
    "ST37_Bottom_Hole_NAD83_10TM_AEP_Forest": ["Well_UWI", "Well_Licence_Number"],
    "ST37_Surface_Hole_NAD83_10TM_AEP_Forest": ["Well_Licence_Number"],
    "ST37_Well_Geometry_NAD83_10TM_AEP_Forest": ["UWI_Label", "Well_Licence_Number"],
}
CATEGORY_COLUMNS = ["Licence_Surface_Location_Label", "Well_Geometry_Source"]


def key_stats(series, name):
    raw = series.astype("string")
    norm = raw.str.strip().str.upper()
    print(f"  {name}: nulls={series.isna().sum():,} blank={(norm == '').sum():,} "
          f"distinct={norm.nunique():,} dup_rows={norm.duplicated(keep=False).sum():,} "
          f"padded={(raw != raw.str.strip()).sum():,} "
          f"matches_uwi_pattern={norm.str.match(UWI_PATTERN).sum():,}")


def main(gdb):
    pd.set_option("display.width", 250)
    available = {name for name, _ in pyogrio.list_layers(gdb)}
    for layer, keys in LAYER_KEYS.items():
        if layer not in available:
            print(f"\n### {layer}: not found, skipped")
            continue
        print(f"\n### {layer}")
        df = pyogrio.read_dataframe(gdb, layer=layer, read_geometry=False)
        for key in keys:
            key_stats(df[key], key)
        for col in CATEGORY_COLUMNS:
            if col in df.columns:
                print(f"  {col}: distinct={df[col].nunique():,} top={df[col].value_counts().head(5).to_dict()}")

        sample = pyogrio.read_dataframe(gdb, layer=layer, max_features=4)
        sample["geometry_wkt"] = sample.geometry.apply(lambda g: g.wkt[:90] if g is not None else None)
        print(sample.drop(columns="geometry").T.to_string())


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_GDB)
