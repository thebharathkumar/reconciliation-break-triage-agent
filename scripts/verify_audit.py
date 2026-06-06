"""CLI to verify the audit chain, with a demo of tamper detection.

Usage:
    python scripts/verify_audit.py [path]          verify a log
    python scripts/verify_audit.py --demo          build a log, tamper, show failure

The demo writes a throwaway log to a temp file, verifies it intact, edits one
line, and verifies again to show the chain breaking at the edited entry.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

# Make the src package importable when run directly from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from recon_triage.audit import (  # noqa: E402
    EVENT_AI_RECOMMENDATION,
    EVENT_BREAK_DETECTED,
    EVENT_HUMAN_DECISION,
    AuditLog,
    verify_chain,
)

DEFAULT_PATH = Path(__file__).resolve().parents[1] / "audit_log.jsonl"


def _report(path: Path, secret: str) -> bool:
    broken = verify_chain(path, secret)
    if broken is None:
        count = sum(1 for line in path.read_text().splitlines() if line.strip()) if path.exists() else 0
        print(f"  OK    chain intact ({count} entries verified) at {path}")
        return True
    print(f"  FAIL  chain broken at entry index {broken} in {path}")
    return False


def run_demo() -> int:
    secret = "demo-secret"
    tmp = Path(tempfile.mkdtemp()) / "demo_audit.jsonl"
    log = AuditLog(tmp, secret=secret)
    log.append(EVENT_BREAK_DETECTED, {"break_id": "BRK-0001", "break_type": "AMOUNT_MISMATCH"})
    log.append(EVENT_AI_RECOMMENDATION, {"break_id": "BRK-0001", "recommended_action": "post_adjustment"})
    log.append(EVENT_HUMAN_DECISION, {"break_id": "BRK-0001", "actor": "ops.analyst", "decision": "approve"})

    print("1. Fresh audit log with three chained entries:")
    _report(tmp, secret)

    print("\n2. Tampering: rewriting the AI recommendation from")
    print("   post_adjustment to write_off, leaving its stored hash in place...")
    lines = tmp.read_text().splitlines()
    entry = json.loads(lines[1])
    entry["payload"]["recommended_action"] = "write_off"
    lines[1] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    tmp.write_text("\n".join(lines) + "\n")

    print("\n3. Re-verifying the tampered log:")
    intact = _report(tmp, secret)

    print()
    if not intact:
        print("Tamper detected. The edited entry no longer matches its HMAC, so the")
        print("resolution trail is provably unaltered when it does verify.")
        return 0
    print("Unexpected: tampering was not detected.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the HMAC-chained audit log.")
    parser.add_argument("path", nargs="?", default=str(DEFAULT_PATH), help="audit log path")
    parser.add_argument("--demo", action="store_true", help="run the tamper-evidence demo")
    args = parser.parse_args()

    if args.demo:
        return run_demo()

    secret = os.environ.get("AUDIT_SECRET", "dev-insecure-audit-secret")
    path = Path(args.path)
    if not path.exists():
        print(f"No audit log at {path}. Run a reconciliation first, or try --demo.")
        return 1
    print(f"Verifying audit chain at {path}")
    return 0 if _report(path, secret) else 2


if __name__ == "__main__":
    raise SystemExit(main())
