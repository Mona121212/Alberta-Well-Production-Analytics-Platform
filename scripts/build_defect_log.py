"""Build the defect table in docs/defect_log.md from config/defect_log.yaml (DEC-024).

The yaml file is the single source of the defect log. This script rewrites the table between the
BEGIN/END markers in the Markdown file; the detail paragraphs below the table are written by hand.

Usage:
    python scripts/build_defect_log.py           rewrite the table
    python scripts/build_defect_log.py --check   fail if the table or the detail paragraphs disagree with the yaml
"""
import argparse
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "defect_log.yaml"
DOC = ROOT / "docs" / "defect_log.md"
BEGIN = "<!-- BEGIN GENERATED: defect table (scripts/build_defect_log.py) -->"
END = "<!-- END GENERATED: defect table -->"
COLUMNS = [("ID", "id"), ("Issue", "issue"), ("Impact", "impact"), ("Handling", "handling"),
           ("Severity", "severity"), ("Status", "status")]


def load(path=CONFIG):
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def format_metric(metric):
    """Text of a metric as it must appear in the detail paragraph, for example 1,310 or 7.1%."""
    return f"{metric['value']:,}" + metric.get("unit", "")


def render_table(cfg):
    lines = ["| " + " | ".join(h for h, _ in COLUMNS) + " |",
             "| " + " | ".join("---" for _ in COLUMNS) + " |"]
    for d in cfg["defects"]:
        cells = [str(d[k]).replace("|", "\\|") for _, k in COLUMNS]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def replace_table(doc_text, table):
    start, end = doc_text.find(BEGIN), doc_text.find(END)
    if start < 0 or end < start:
        raise ValueError(f"{DOC.name} must contain the markers {BEGIN!r} and {END!r}")
    return doc_text[:start + len(BEGIN)] + "\n\n" + table + "\n\n" + doc_text[end:]


def detail_paragraphs(doc_text):
    """Map each defect id to its detail paragraph, which starts with **D-xxx ..."""
    parts = re.split(r"(?m)^(?=\*\*D-\d{3}\b)", doc_text)
    return {re.match(r"\*\*(D-\d{3})", p).group(1): p for p in parts if p.startswith("**D-")}


def missing_metrics(cfg, doc_text):
    """Metrics from the yaml whose value does not appear in the detail paragraph of their defect."""
    paragraphs = detail_paragraphs(doc_text)
    problems = []
    for d in cfg["defects"]:
        text = paragraphs.get(d["id"])
        if text is None:
            problems.append(f"{d['id']}: no detail paragraph")
            continue
        for m in d.get("metrics", []):
            value = format_metric(m)
            # Whole number only: 26 must not match inside 1,262 or 2.6
            if not re.search(r"(?<![\d.,])" + re.escape(value) + r"(?![\d]|[.,]\d)", text):
                problems.append(f"{d['id']}: metric {m['name']} = {value} not found in its detail paragraph")
    return problems


def check(cfg, doc_text):
    problems = []
    if replace_table(doc_text, render_table(cfg)) != doc_text:
        problems.append("defect table is out of date: run python scripts/build_defect_log.py")
    return problems + missing_metrics(cfg, doc_text)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true", help="only check, do not write")
    args = parser.parse_args()

    cfg = load()
    doc_text = DOC.read_text(encoding="utf-8")
    if args.check:
        problems = check(cfg, doc_text)
        for p in problems:
            print("FAIL", p)
        print("defect log check:", "FAIL" if problems else "PASS", f"({len(cfg['defects'])} defects)")
        return 1 if problems else 0

    DOC.write_text(replace_table(doc_text, render_table(cfg)), encoding="utf-8", newline="\n")
    print(f"wrote {DOC.relative_to(ROOT)}: {len(cfg['defects'])} defects")
    return 0


if __name__ == "__main__":
    sys.exit(main())
