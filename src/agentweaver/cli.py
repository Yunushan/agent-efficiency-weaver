"""CLI entry point."""
import argparse
import json
import sys
from pathlib import Path
from .config import load_json, render, write_generated, validate_profile, validate_catalog, WeaverError
from .report import read_runs, summarize, as_markdown
from .compare import compare_runs

def root_path():
    return Path(__file__).resolve().parents[2]

def main(argv=None):
    parser = argparse.ArgumentParser(prog="agentweaver")
    subs = parser.add_subparsers(dest="cmd", required=True)
    check = subs.add_parser("validate")
    check.add_argument("--root", type=Path, default=root_path())
    render_cmd = subs.add_parser("render")
    render_cmd.add_argument("--root", type=Path, default=root_path())
    render_cmd.add_argument("--platform", required=True, choices=["kilo", "abacus"])
    render_cmd.add_argument("--profile", required=True, choices=["efficient", "balanced", "enterprise-secure"])
    render_cmd.add_argument("--output-dir", type=Path, default=Path("generated"))
    render_cmd.add_argument("--enable", action="append", default=[])
    render_cmd.add_argument("--dry-run", action="store_true")
    render_cmd.add_argument("--force", action="store_true")
    rp = subs.add_parser("report")
    rp.add_argument("--input", type=Path, required=True)
    rp.add_argument("--format", choices=["json","markdown"], default="markdown")
    rp.add_argument("--output", type=Path)
    cp = subs.add_parser("compare")
    cp.add_argument("--input", type=Path, required=True)
    cp.add_argument("--agent", required=True)
    cp.add_argument("--source", choices=["measured","synthetic"], default="measured")
    cp.add_argument("--baseline", default="baseline")
    cp.add_argument("--candidate", default="optimized")
    cp.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.cmd == "validate":
            catalog = load_json(args.root / "catalogs/mcp-servers.json")
            errors = validate_catalog(catalog)
            for profile_path in sorted((args.root / "profiles").glob("*.json")):
                errors.extend(validate_profile(load_json(profile_path), catalog))
            if errors:
                for error in errors:
                    print("ERROR:", error, file=sys.stderr)
                return 1
            print("Configuration validation passed")
            return 0
        if args.cmd in ("report", "compare"):
            if args.cmd == "report":
                result = summarize(read_runs(args.input))
                body = json.dumps(result, indent=2) + "\\n" if args.format == "json" else as_markdown(result)
            else:
                result = compare_runs(read_runs(args.input), agent=args.agent, source=args.source,
                                      baseline=args.baseline, candidate=args.candidate)
                body = json.dumps(result, indent=2) + "\\n"
            if args.output:
                args.output.parent.mkdir(parents=True, exist_ok=True)
                args.output.write_text(body, encoding="utf-8")
                print("Generated", args.output)
            else:
                print(body, end="")
            return 0
        catalog = load_json(args.root / "catalogs/mcp-servers.json")
        profile = load_json(args.root / "profiles" / (args.profile + ".json"))
        generated = render(args.platform, profile, catalog, args.enable)
        if args.dry_run:
            print(json.dumps(generated, indent=2))
        else:
            print(write_generated(args.output_dir, args.platform, generated, force=args.force))
        return 0
    except WeaverError as exc:
        print("ERROR:", exc, file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
