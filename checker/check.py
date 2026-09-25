"""Compliance checker for internal LLM apps.

Usage:
    python checker/check.py PATH_TO_APP [--no-ai] [--json]

Exit codes: 0 = safe to ship, 1 = blocked (a plain check failed),
2 = nothing blocking, but a human must review the flagged items first.
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from judge import MODEL, run_judge
from rules import RULES, Result, load_app, read_manifest

EXIT_CODES = {"PASS": 0, "FAIL": 1, "REVIEW": 2}
VERDICTS = {
    "PASS": "Safe to ship.",
    "FAIL": "Blocked: fix the failures and re-run.",
    "REVIEW": "Nothing blocking, but a human must review the flagged items first.",
}


def overall(results: list[Result]) -> str:
    statuses = {r.status for r in results}
    if "FAIL" in statuses:
        return "FAIL"
    if "REVIEW" in statuses:
        return "REVIEW"
    return "PASS"


def next_step(manifest: dict | None, verdict: str) -> str:
    """Same checker for both tracks; the manifest decides who acts on the result."""
    if not manifest:
        return "Add a compliance.toml so the result can be routed to an owner."
    owner, contact = manifest.get("owner_team", "the owning team"), manifest.get("contact", "?")
    if verdict == "FAIL":
        return f"Back to {owner} ({contact}) to fix."
    if verdict == "REVIEW":
        return f"Queued for a platform reviewer; {owner} is notified at {contact}."
    if manifest.get("track") == "central":
        return "Hand-off: the central team owns alerts, deploys and rollbacks from here."
    return f"{owner} owns alerts, deploys and rollbacks; alerts go to {contact}."


def print_section(heading: str, results: list[Result]) -> None:
    print(f"\n{heading}")
    for r in results:
        print(f"  {r.status:<7} {r.title}")
        if r.status not in ("PASS", "CLEAR"):
            print(f"          {r.detail}")
            for e in r.evidence:
                print(f"          -> {e}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check an LLM app against the 'safe to ship' bar.")
    parser.add_argument("app", type=Path, help="folder containing the app's source code")
    parser.add_argument("--no-ai", action="store_true", help="run the plain checks only, skip the AI judge")
    parser.add_argument("--json", action="store_true", help="print a machine-readable report")
    args = parser.parse_args()
    if not args.app.is_dir():
        parser.error(f"{args.app} is not a folder")

    app = load_app(args.app)
    static = [rule(app) for rule in RULES]
    ai, notes = ([], ["AI judge skipped (--no-ai)"]) if args.no_ai else run_judge(app)
    verdict = overall(static + ai)
    manifest, _ = read_manifest(app)

    if args.json:
        report = {
            "app": str(args.app),
            "verdict": verdict,
            "next_step": next_step(manifest, verdict),
            "plain_checks": [asdict(r) for r in static],
            "ai_judge": [asdict(r) for r in ai],
            "notes": notes,
        }
        print(json.dumps(report, indent=2))
    else:
        print(f"Compliance check: {args.app} (files read: {len(app.files)})")
        print_section("Plain checks (these decide PASS / FAIL)", static)
        if ai:
            print_section(f"AI judge, {MODEL} (can only flag items for a human)", ai)
        for note in notes:
            print(f"  note: {note}")
        print(f"\nResult: {verdict}. {VERDICTS[verdict]}")
        print(f"Next:   {next_step(manifest, verdict)}")
    sys.exit(EXIT_CODES[verdict])


if __name__ == "__main__":
    main()
