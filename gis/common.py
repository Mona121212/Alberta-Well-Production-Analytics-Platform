"""Shared helpers: config loading, run mode, output paths, logging."""
import argparse
import logging
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = REPO_ROOT / "config" / "gis.yaml"
MODES = ("sample", "full")


def get_logger(name):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return logging.getLogger(name)


def base_arg_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="Path to gis.yaml")
    parser.add_argument("--mode", choices=MODES, help="Override run.mode from the config")
    return parser


def load_config(path, mode_override=None):
    with open(path, encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    mode = mode_override or cfg["run"]["mode"]
    if mode not in MODES:
        raise ValueError(f"run.mode must be one of {MODES}, got {mode!r}")
    cfg["run"]["mode"] = mode
    return cfg


def repo_path(relative):
    return REPO_ROOT / relative


def output_dir(cfg):
    """Mode-specific output folder, so sample and full runs never overwrite each other."""
    path = repo_path(cfg["paths"]["output_root"]) / cfg["run"]["mode"]
    path.mkdir(parents=True, exist_ok=True)
    return path


def staging_dir(cfg):
    path = output_dir(cfg) / "staging"
    path.mkdir(parents=True, exist_ok=True)
    return path
