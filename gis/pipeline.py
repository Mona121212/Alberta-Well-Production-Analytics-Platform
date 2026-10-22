"""Run the GIS pipeline end to end: stage -> convert -> qa.

A FAIL-level QA rule stops the run with exit code 1; later steps do not run.

Usage:
    python -m gis.pipeline [--mode sample|full] [--force-stage]
"""
import sys

from gis import convert, qa, stage
from gis.common import base_arg_parser, get_logger, load_config

log = get_logger("gis.pipeline")


def main():
    parser = base_arg_parser(__doc__)
    parser.add_argument("--qa-config", default=str(qa.DEFAULT_QA_CONFIG), help="Path to gis_qa.yaml")
    parser.add_argument("--force-stage", action="store_true", help="Re-read the raw GDB even if staged")
    args = parser.parse_args()
    cfg = load_config(args.config, args.mode)

    stage.run(cfg, force=args.force_stage)
    convert.run(cfg)
    try:
        qa.run(cfg, qa.load_qa_config(args.qa_config))
    except qa.QAFailed as exc:
        log.error("Pipeline stopped at QA: %s", exc)
        sys.exit(1)
    log.info("Pipeline finished (%s mode)", cfg["run"]["mode"])


if __name__ == "__main__":
    main()
