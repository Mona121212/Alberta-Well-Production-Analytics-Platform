"""Liability and compliance screening: abandoned-not-reclaimed wells, inactive candidates, licensee profile.

Joins the standardized ST37 wells (one per licence) to Petrinex well activity through the
UWI -> Petrinex well key rule used in Silver. Working definitions are in config/gis_liability.yaml
(screening only, not AER regulatory definitions).

Outputs in gis/output/<mode>/liability/:
- liability.gpkg               wells_liability, abandoned_not_reclaimed, inactive_candidates, abandoned_by_township
- abandoned_by_licensee.csv, abandoned_by_township.csv
- petrinex_match_summary.csv, petrinex_unmatched_wells.csv
- licensee_profile.csv, top_inactive_licensees.csv
- liability_reconciliation.csv (a failed test stops the pipeline after outputs are written)

Usage:
    python -m gis.liability [--mode sample|full]
"""
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
import pyogrio
import yaml

from gis.common import REPO_ROOT, base_arg_parser, get_logger, load_config, output_dir, reset_gpkg, staging_dir
from gis.convert import standardize_attributes
from gis.spatial import assign_pads

log = get_logger("gis.liability")
DEFAULT_LIABILITY_CONFIG = REPO_ROOT / "config" / "gis_liability.yaml"


class ReconciliationFailed(Exception):
    """Raised when a liability reconciliation test fails."""


# --- Inputs --------------------------------------------------------------------------------

def load_wells(cfg):
    wells_layer = cfg["layers"][cfg["wells_layer"]]["output_layer"]
    return pyogrio.read_dataframe(output_dir(cfg) / cfg["exports"]["geopackage"], layer=wells_layer, use_arrow=True)


def load_reference(cfg, key):
    df = pd.read_parquet(staging_dir(cfg) / f"{key}_reference.parquet")
    return standardize_attributes(df, cfg["layers"][key]["rename"])


def load_petrinex(cfg, lcfg):
    pet = pd.read_parquet(staging_dir(cfg) / "petrinex_wells.parquet")
    masked = lcfg["petrinex"]["masked_value"]
    pet["well_key"] = pet["FromToIDIdentifier"].str.strip().str.upper()
    months = {m: pd.Period(m, freq="M") for m in pet["ProductionMonth"].unique()}  # 12 values, 4M rows
    pet["month"] = pet["ProductionMonth"].map(months).astype("period[M]")
    pet["volume"] = pd.to_numeric(pet["Volume"].where(pet["Volume"] != masked), errors="coerce")
    pet["hours"] = pd.to_numeric(pet["Hours"].where(pet["Hours"] != masked), errors="coerce")
    return pet


# --- Petrinex well activity ---------------------------------------------------------------

def volume_columns(lcfg):
    """Product -> output column, e.g. OIL -> oil_m3_12m (production volume in the longest window)."""
    n = max(lcfg["windows_months"])
    return {product: f"{unit}_{n}m" for product, unit in lcfg["petrinex"]["volume_products"].items()}


def well_activity(pet, lcfg, window_start):
    """One row per Petrinex well key: last month with production / activity / any report,
    and production volume per product inside the window."""
    max_hours = pet["month"].dt.days_in_month * 24
    gold_reject = (pet["ActivityID"] == "PROD") & (pet["hours"] > max_hours)  # same as Silver hours rule
    positive = (pet["volume"] > 0) & ~gold_reject
    is_prod = positive & pet["ActivityID"].isin(lcfg["petrinex"]["production_activities"])
    is_active = positive & pet["ActivityID"].isin(lcfg["petrinex"]["activity_activities"])

    by_key = pet.groupby("well_key")
    activity = pd.DataFrame({
        "last_report_month": by_key["month"].max(),
        "last_prod_month": pet[is_prod].groupby("well_key")["month"].max(),
        "last_activity_month": pet[is_active].groupby("well_key")["month"].max(),
        "prod_months": pet[is_prod].groupby("well_key")["month"].nunique(),
    })
    vol_cols = volume_columns(lcfg)
    in_window = is_prod & (pet["month"] >= window_start) & pet["ProductID"].isin(list(vol_cols))
    volumes = (pet[in_window].pivot_table(index="well_key", columns="ProductID", values="volume", aggfunc="sum")
               .reindex(columns=list(vol_cols)).rename(columns=vol_cols))
    activity = activity.join(volumes)
    activity["prod_months"] = activity["prod_months"].fillna(0).astype(int)
    activity["key_valid"] = activity.index.str.fullmatch(lcfg["petrinex"]["key_pattern"].strip("^$"))
    return activity.rename_axis("well_key").reset_index()


def uwi_to_well_key(uwi, pattern):
    parts = uwi.str.upper().str.extract(pattern)
    key = "1" + parts[0] + parts[1] + parts[2] + parts[3] + parts[4] + "W" + parts[5] + parts[6].str.zfill(2)
    return key.where(parts.notna().all(axis=1))


def well_key_map(cfg, lcfg):
    """Petrinex well key -> ST37 UWI and licence, from production strings and bottom holes (whole province)."""
    frames = []
    for key in ("production_strings", "bottom_holes"):
        ref = load_reference(cfg, key)[["uwi", "licence_number"]].dropna()
        frames.append(ref.assign(st37_source=key))
    mapping = pd.concat(frames, ignore_index=True)
    mapping["well_key"] = uwi_to_well_key(mapping["uwi"], lcfg["uwi_pattern"])
    # Production strings first: they are the event-level source (see DECISIONS.md DEC-001).
    return mapping.dropna(subset=["well_key"]).drop_duplicates("well_key")


# --- Petrinex -> ST37 match rate ----------------------------------------------------------

def match_petrinex(activity, key_map, surface_licences, window_start, volume_cols):
    """Classify every Petrinex well with production in the window by how it joins to ST37."""
    producing = activity[activity["last_prod_month"] >= window_start].merge(key_map, on="well_key", how="left")
    location_keys = set(key_map["well_key"].str[:-2])
    has_surface = producing["licence_number"].isin(surface_licences)
    producing["match_status"] = np.select(
        [~producing["key_valid"],
         producing["uwi"].notna() & has_surface,
         producing["uwi"].notna(),
         producing["well_key"].str[:-2].isin(location_keys)],
        ["unmatched: invalid key format",
         "matched: well and surface licence",
         "unmatched: UWI found, licence not in surface holes",
         "unmatched: location found, different event sequence"],
        default="unmatched: not in ST37 production strings or bottom holes")

    summary = producing.groupby("match_status").agg(wells=("well_key", "size"), **{
        c: (c, "sum") for c in volume_cols})
    summary["pct_wells"] = (100 * summary["wells"] / summary["wells"].sum()).round(3)
    for c in volume_cols:
        summary[f"pct_{c}"] = (100 * summary[c] / summary[c].sum()).round(3)
    summary = summary.reset_index().sort_values("wells", ascending=False)
    return producing, summary


# --- Well classification ------------------------------------------------------------------

def licence_activity(key_map, activity, volume_cols):
    joined = key_map.merge(activity, on="well_key", how="inner")
    return joined.groupby("licence_number").agg(
        petrinex_well_keys=("well_key", "nunique"),
        last_report_month=("last_report_month", "max"),
        last_prod_month=("last_prod_month", "max"),
        last_activity_month=("last_activity_month", "max"),
        **{c: (c, "sum") for c in volume_cols},
    ).reset_index()


def apply_pad_review(w, pad_distance, review_cfg):
    """Move no-activity wells on large never-reported pads to their own review class."""
    # All licences (including cancelled) define the review pads; gis.analysis builds drilled-well pads.
    w["review_pad_id"], _ = assign_pads(w, pad_distance)
    counted = ~w["licence_status_group"].isin(review_cfg["count_excludes_groups"])
    w["pad_licences"] = counted.groupby(w["review_pad_id"]).transform("sum").fillna(0).astype(int)
    pad_reported = w["in_petrinex"].groupby(w["review_pad_id"]).transform("any").astype(bool)
    review = (w["well_class"].isin(["active_no_activity_6m", "active_no_activity_12m"]) & ~w["in_petrinex"]
              & ~pad_reported & (w["pad_licences"] >= review_cfg["min_licences"]))
    w.loc[review, "well_class"] = review_cfg["well_class"]
    return w


def classify(wells, lic_activity, lcfg, as_of, pad_distance):
    start_6 = as_of - (min(lcfg["windows_months"]) - 1)
    start_12 = as_of - (max(lcfg["windows_months"]) - 1)
    w = wells.merge(lic_activity, on="licence_number", how="left")

    base = w["licence_status_group"].map(lcfg["status_classes"]).fillna("unmapped")
    spud = pd.to_datetime(w["spud_date"])
    spud_month = spud.dt.to_period("M")
    last = w["last_activity_month"]
    honour_spud = lcfg["require_spud_before_window"]
    active = base == "active"

    w["well_class"] = np.select(
        [~active,
         spud.isna(),
         last >= start_6,
         honour_spud & (spud_month >= start_6),
         last >= start_12,
         honour_spud & (spud_month >= start_12)],
        [base, "active_not_spudded", "active_reporting", "active_recently_spudded",
         "active_no_activity_6m", "active_no_activity_6m"],
        default="active_no_activity_12m")
    w["in_petrinex"] = w["petrinex_well_keys"].notna()
    w = apply_pad_review(w, pad_distance, lcfg["pad_review"])
    w["inactive_candidate_6m"] = w["well_class"].isin(["active_no_activity_6m", "active_no_activity_12m"])
    w["inactive_candidate_12m"] = w["well_class"] == "active_no_activity_12m"
    w["petrinex_evidence"] = np.select(
        [last >= start_12, w["last_report_month"] >= start_12],
        ["volume reported", "reported without volume (e.g. SHUTIN)"],
        default="no Petrinex record in window")

    as_of_date = as_of.to_timestamp(how="end").normalize()
    w["years_since_spud"] = ((as_of_date - spud).dt.days / 365.25).round(1)
    w["years_in_status"] = ((as_of_date - pd.to_datetime(w["licence_status_date"])).dt.days / 365.25).round(1)
    for col in ("last_report_month", "last_prod_month", "last_activity_month"):
        w[col] = w[col].astype("string")
    w["petrinex_well_keys"] = w["petrinex_well_keys"].fillna(0).astype(int)
    dls = w["surface_location_dls"].str.extract(r"^\d{2}-\d{2}-(\d{3})-(\d{2})(W\d)$")
    w["township"] = dls[0] + "-" + dls[1] + dls[2]
    return gpd.GeoDataFrame(w, geometry="geometry", crs=wells.crs)


# --- Summaries ----------------------------------------------------------------------------

def abandoned_summary(abandoned, by):
    grouped = abandoned.assign(**{by: abandoned[by].fillna("(missing)")}).groupby(by)
    summary = grouped.agg(
        abandoned_not_reclaimed=("licence_number", "size"),
        median_years_since_spud=("years_since_spud", "median"),
        max_years_since_spud=("years_since_spud", "max"),
        median_years_in_status=("years_in_status", "median"),
        wells_10y_plus_in_status=("years_in_status", lambda s: int((s >= 10).sum())),
    ).reset_index().sort_values("abandoned_not_reclaimed", ascending=False)
    return summary.round(1)


def township_points(abandoned, summary):
    """Mean position of the abandoned wells in each township (not the township centre)."""
    centroids = abandoned.dropna(subset=["township"]).dissolve(by="township").centroid.rename("geometry")
    return gpd.GeoDataFrame(summary.merge(centroids.reset_index(), on="township"), geometry="geometry",
                            crs=abandoned.crs)


def licensee_profile(classified, lcfg):
    prof = lcfg["profile"]
    counts = (classified.assign(licensee=classified["licensee"].fillna("(missing)"))
              .pivot_table(index="licensee", columns="well_class", values="licence_number",
                           aggfunc="count", fill_value=0))
    out = pd.DataFrame(index=counts.index)
    out["wells"] = counts.sum(axis=1)
    groups = ["active", "inactive", "abandoned", "reclaimed", "review"]
    for group in groups:
        out[group] = counts.reindex(columns=prof[group], fill_value=0).sum(axis=1)
    out["other"] = out["wells"] - out[groups].sum(axis=1)
    big = out["wells"] >= prof["min_wells_for_ratio"]
    out["inactive_ratio"] = (out["inactive"] / (out["active"] + out["inactive"])).where(big).round(3)
    out["unreclaimed_ratio"] = (out["abandoned"] / (out["abandoned"] + out["reclaimed"])).where(big).round(3)
    out = out.join(counts.add_prefix("class_"))
    return out.reset_index().sort_values("wells", ascending=False)


# --- Reconciliation -----------------------------------------------------------------------

def reconciliation(pet, wells, classified, profile, producing, lcfg):
    rec = lcfg["reconciliation"]
    month = pet[pet["month"] == pd.Period(rec["month"], freq="M")]
    prod_keys = month.loc[month["ActivityID"] == "PROD", "well_key"].nunique()
    tests = [
        (f"petrinex_{rec['month']}_distinct_well_keys_match_fabric", rec["expected_distinct_well_keys"],
         month["well_key"].nunique()),
        (f"petrinex_{rec['month']}_producing_well_keys_match_fabric", rec["expected_producing_well_keys"], prod_keys),
        ("classified_wells_equal_input_wells", len(wells), len(classified)),
        ("classified_licences_unique", len(classified), classified["licence_number"].nunique()),
        ("profile_wells_sum_equals_wells", len(classified), int(profile["wells"].sum())),
        ("match_join_has_no_fan_out", producing["well_key"].nunique(), len(producing)),
    ]
    return pd.DataFrame([(n, int(e), int(a), "PASS" if int(e) == int(a) else "FAIL") for n, e, a in tests],
                        columns=["test_name", "expected", "actual", "status"])


# --- Run ----------------------------------------------------------------------------------

def run(cfg, lcfg):
    out = output_dir(cfg) / "liability"
    out.mkdir(parents=True, exist_ok=True)

    pet = load_petrinex(cfg, lcfg)
    as_of = pet["month"].max() if lcfg["as_of_month"] == "latest" else pd.Period(lcfg["as_of_month"], freq="M")
    log.info("Petrinex months %s to %s; as-of month %s", pet["month"].min(), pet["month"].max(), as_of)

    window_start = as_of - (max(lcfg["windows_months"]) - 1)
    vol_cols = list(volume_columns(lcfg).values())
    activity = well_activity(pet, lcfg, window_start)
    key_map = well_key_map(cfg, lcfg)
    surface_licences = set(load_reference(cfg, "surface_holes")["licence_number"].dropna())
    producing, match_summary = match_petrinex(activity, key_map, surface_licences, window_start, vol_cols)

    wells = load_wells(cfg)
    classified = classify(wells, licence_activity(key_map, activity, vol_cols), lcfg, as_of,
                          cfg["pads"]["distance_m"])
    abandoned = classified[classified["well_class"] == "abandoned"]
    inactive = classified[classified["inactive_candidate_6m"]]
    by_licensee = abandoned_summary(abandoned, "licensee")
    by_township = abandoned_summary(abandoned, "township")
    profile = licensee_profile(classified, lcfg)
    top = profile.sort_values(["inactive", "wells"], ascending=False).head(lcfg["profile"]["top_n"])
    checks = reconciliation(pet, wells, classified, profile, producing, lcfg)

    gpkg = out / "liability.gpkg"
    reset_gpkg(gpkg, log)
    for layer, gdf in [("wells_liability", classified), ("abandoned_not_reclaimed", abandoned),
                       ("inactive_candidates", inactive),
                       ("abandoned_by_township", township_points(abandoned, by_township))]:
        pyogrio.write_dataframe(gdf, gpkg, layer=layer, driver="GPKG", use_arrow=True)
    by_licensee.to_csv(out / "abandoned_by_licensee.csv", index=False)
    by_township.to_csv(out / "abandoned_by_township.csv", index=False)
    match_summary.to_csv(out / "petrinex_match_summary.csv", index=False)
    producing[~producing["match_status"].str.startswith("matched")].to_csv(
        out / "petrinex_unmatched_wells.csv", index=False)
    profile.to_csv(out / "licensee_profile.csv", index=False)
    top.to_csv(out / "top_inactive_licensees.csv", index=False)
    checks.to_csv(out / "liability_reconciliation.csv", index=False)

    log.info("Well classes: %s", classified["well_class"].value_counts().to_dict())
    log.info("Petrinex producing wells (%d months): %s; match: %s", max(lcfg["windows_months"]),
             f"{len(producing):,}", match_summary.set_index("match_status")["pct_wells"].to_dict())
    log.info("Outputs: %s", out)
    for row in checks.itertuples():
        log.info("reconciliation %-55s expected %s actual %s %s", row.test_name, f"{row.expected:,}",
                 f"{row.actual:,}", row.status)
    if (checks["status"] == "FAIL").any():
        raise ReconciliationFailed(", ".join(checks.loc[checks["status"] == "FAIL", "test_name"]))


def load_liability_config(path=DEFAULT_LIABILITY_CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--liability-config", default=str(DEFAULT_LIABILITY_CONFIG))
    args = parser.parse_args()
    try:
        run(load_config(args.config, args.mode), load_liability_config(args.liability_config))
    except ReconciliationFailed as exc:
        log.error("Reconciliation failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
