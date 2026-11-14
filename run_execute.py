#!/usr/bin/env python3
"""CLI: execute approved Hermes Organizer proposals (v0.7.0).

Default is DRY-RUN: prints exactly what would happen (rename X -> Y,
move A -> B, folders that would be created) with zero Drive write
calls. Pass --execute to perform it.

Usage:
  # 1. Scan (read-only) and emit a proposals file:
  python run_scan.py --drive --folder <folder_id> --limit 20 \\
      --report sample_reports/scan.json \\
      --proposals sample_reports/proposals.json

  # 2. Approve: copy proposals to approvals and flip approved to true
  #    on the entries you accept. Do not edit filenames or folders —
  #    the proposal hash catches that and the run is refused.
  cp sample_reports/proposals.json sample_reports/approvals.json

  # 3. Dry-run (default): shows the plan, writes nothing.
  python run_execute.py --proposals sample_reports/proposals.json \\
      --approvals sample_reports/approvals.json --log execution-log.jsonl

  # 4. Execute for real (needs --execute; refuses without it).
  python run_execute.py --proposals sample_reports/proposals.json \\
      --approvals sample_reports/approvals.json --log execution-log.jsonl \\
      --execute

  # 5. Roll back from the execution log (dry-run unless --execute).
  python run_execute.py --rollback execution-log.jsonl [--execute]

  # 6. Verify a past execution log against live Drive (read-only).
  python run_execute.py --verify execution-log.jsonl

Exit codes: 0 ok; 1 refused (gates, hash mismatch, collision,
verification mismatch) or error; 2 bad arguments / unreadable files.
Nothing is ever deleted. The scan path (run_scan.py) never writes.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hermes_organizer import execution  # noqa: E402
from hermes_organizer.execution import DriveWriter  # noqa: E402
from hermes_organizer.proposals import load_approvals, load_proposals  # noqa: E402


def _print_plan(planned: dict) -> None:
    if planned["refused"]:
        print("REFUSED — nothing would happen:")
        for problem in planned["refused"]:
            print(f"  - {problem}")
    for step in planned["steps"]:
        print(
            f"  rename+move: {step['original_name']!r} -> "
            f"{step['proposed_filename']!r} in {step['target_folder_path']}"
        )
    for folder in planned["folders_to_create"]:
        print(f"  create folder: {folder}")
    for item in planned["skipped"]:
        print(f"  skip: {item['original_name']!r} ({item['reason']})")
    if not planned["refused"] and not planned["steps"]:
        print("  (nothing to do — every file is skipped)")


def _print_verification(verification: list[dict]) -> int:
    bad = 0
    for entry in verification:
        status = "ok" if entry["ok"] else "MISMATCH"
        if not entry["ok"]:
            bad += 1
        print(
            f"  [{status}] {entry['expected_name']!r}: "
            f"Drive now shows {entry['actual_name']!r} in "
            f"{entry['actual_parents']}"
        )
    return bad


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Hermes Organizer v0.7.0 — execute approved proposals"
    )
    ap.add_argument("--proposals", default=None,
                    help="Proposals file from run_scan.py --proposals")
    ap.add_argument("--approvals", default=None,
                    help="Approvals file (copy of proposals with approved=true)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the plan only (this is also the default)")
    ap.add_argument("--execute", action="store_true",
                    help="Perform the renames + moves for real")
    ap.add_argument("--rollback", default=None,
                    help="Execution log (JSONL) to restore from")
    ap.add_argument("--verify", default=None,
                    help="Execution log (JSONL) to verify against live Drive")
    ap.add_argument("--log", default="execution-log.jsonl",
                    help="Execution log path (default execution-log.jsonl)")
    ap.add_argument("--root", default="root",
                    help="Drive folder ID the target paths resolve from "
                    "(default: root = My Drive)")
    args = ap.parse_args()

    drive = DriveWriter()

    if args.verify:
        try:
            verification = execution.verify_log(args.verify, drive)
        except (OSError, ValueError) as exc:
            print(f"ERROR: cannot verify {args.verify}: {exc}", file=sys.stderr)
            return 2
        print(f"Verification of {args.verify}:")
        bad = _print_verification(verification)
        print(f"{len(verification) - bad}/{len(verification)} verified ok.")
        return 1 if bad else 0

    if args.rollback:
        if args.execute:
            # The runtime gate: only --execute on the command line
            # flips WRITE_ENABLED. rollback() checks it before writing.
            execution.WRITE_ENABLED = True
        try:
            report = execution.rollback(
                args.rollback, drive, execute=args.execute
            )
        except execution.ExecutionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        except OSError as exc:
            print(f"ERROR: cannot read {args.rollback}: {exc}", file=sys.stderr)
            return 2
        if report.get("refused"):
            for problem in report["refused"]:
                print(f"REFUSED: {problem}")
            for item in report.get("would_restore", []):
                print(
                    f"  would restore: {item['file_id']} -> "
                    f"{item['restore_name']!r} in {item['restore_parent_ids']}"
                )
            return 1
        for item in report["restored"]:
            print(
                f"  restored: {item['file_id']} -> "
                f"{item['restored_name']!r}"
            )
        for item in report["missing"]:
            print(f"  missing: {item['file_id']} ({item['reason']})")
        print(f"Rollback complete: {len(report['restored'])} restored, "
              f"{len(report['missing'])} missing.")
        return 0

    if not args.proposals or not args.approvals:
        ap.error("--proposals and --approvals are required "
                 "(or use --rollback / --verify)")
    try:
        proposals_doc = load_proposals(args.proposals)
        approvals_doc = load_approvals(args.approvals)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    if args.execute:
        execution.WRITE_ENABLED = True
        try:
            report = execution.execute(
                proposals_doc, approvals_doc, drive, args.log,
                execute=True, root_folder_id=args.root,
            )
        except execution.ExecutionError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        for record in report["executed"]:
            print(
                f"  executed: {record['original_name']!r} -> "
                f"{record['new_name']!r} in {record['new_parent_ids']}"
            )
        for folder_id in report["folders_created"]:
            print(f"  created folder: {folder_id}")
        for item in report["skipped"]:
            print(f"  skip: {item['original_name']!r} ({item['reason']})")
        print(f"\nExecuted {len(report['executed'])} file(s). "
              f"Log: {args.log}")
        print("Verification (re-fetched from Drive):")
        bad = _print_verification(report["verification"])
        if bad:
            print(f"WARNING: {bad} file(s) did not verify — see above. "
                  "The log still records what was attempted; use "
                  "--rollback to undo.")
            return 1
        print("All executed files verified ok.")
        return 0

    planned = execution.plan(
        proposals_doc, approvals_doc, drive, root_folder_id=args.root
    )
    print("Dry-run — nothing was renamed, moved, or created:")
    _print_plan(planned)
    if planned["refused"]:
        return 1
    print("\nRe-run with --execute to perform this plan.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
