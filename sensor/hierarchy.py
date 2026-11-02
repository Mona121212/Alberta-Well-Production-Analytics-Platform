"""Asset hierarchy facility -> well -> tag, with PI AF style paths (DEC-032)."""
import pandas as pd

from sensor.config import tag_name, tag_specs, well_tag_codes

COLUMNS = ["node_type", "af_path", "parent_path", "facility_id", "well_key", "product", "tag_code", "tag_name",
           "attribute", "unit", "kind", "physical_min", "physical_max", "normal_min", "normal_max"]


def build_hierarchy(cfg):
    """One row per node: facilities, wells and tags. Tag rows carry the tag name, unit and ranges."""
    root = cfg["af"]["root"]
    specs = tag_specs(cfg)
    rows = []
    for facility in sorted({w["facility_id"] for w in cfg["wells"]}):
        rows.append({"node_type": "facility", "af_path": f"{root}\\{facility}", "parent_path": root,
                     "facility_id": facility})
    for well in cfg["wells"]:
        well_path = f"{root}\\{well['facility_id']}\\{well['well_key']}"
        rows.append({"node_type": "well", "af_path": well_path, "parent_path": f"{root}\\{well['facility_id']}",
                     "facility_id": well["facility_id"], "well_key": well["well_key"], "product": well["product"]})
        for code in well_tag_codes(cfg, well):
            spec = specs[code]
            rows.append({"node_type": "tag", "af_path": f"{well_path}|{spec['attribute']}", "parent_path": well_path,
                         "facility_id": well["facility_id"], "well_key": well["well_key"], "product": well["product"],
                         "tag_code": code, "tag_name": tag_name(cfg, well, code), "attribute": spec["attribute"],
                         "unit": spec["unit"], "kind": spec["kind"],
                         "physical_min": float(spec["physical_range"][0]),
                         "physical_max": float(spec["physical_range"][1]),
                         "normal_min": float(spec["normal_range"][0]), "normal_max": float(spec["normal_range"][1])})
    return pd.DataFrame(rows, columns=COLUMNS)
