"""Run with Python 3.11+; core simulation has no third-party dependencies."""
import argparse
import json
from dataclasses import replace

from thermallink.models import Config
from thermallink.reporting import save_results
from thermallink.simulation import SCENARIOS, run_simulation


def main():
    parser = argparse.ArgumentParser(description="THERMAL LINK Rev.C virtual simulator")
    parser.add_argument("--scenario", choices=SCENARIOS, default="normal")
    parser.add_argument("--all", action="store_true", help="run every fault scenario")
    parser.add_argument("--duration", type=float, default=600.0, help="virtual seconds")
    parser.add_argument("--config", help="JSON configuration; defaults are hypothetical")
    parser.add_argument("--duty", type=float, help="manual requested PWM percent; omitted = BACK PI control")
    parser.add_argument("--controller", choices=("pi", "neural"), default="pi")
    parser.add_argument("--ambient-profile", choices=("constant", "triangle"))
    parser.add_argument("--output", default="outputs", help="output folder")
    args = parser.parse_args()
    try:
        cfg = Config.load(args.config) if args.config else Config()
        if args.ambient_profile:
            cfg = replace(cfg, ambient_profile=args.ambient_profile)
        names = list(SCENARIOS) if args.all else [args.scenario]
        results = [run_simulation(name, args.duration, cfg, args.duty, args.controller) for name in names]
        report = save_results(results, args.output)
    except (ValueError, TypeError, KeyError, OSError) as exc:
        parser.error(str(exc))
    print(json.dumps([r.summary() for r in results], ensure_ascii=True, indent=2))
    print(f"Report: {report.resolve()}")


if __name__ == "__main__":
    main()
