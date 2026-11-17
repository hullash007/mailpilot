#!/usr/bin/env python3
"""Run a bounded slice of the persistent sample-only AI queue (Linux/Unix).

HTTP POST /refine/{account_id} only enqueues. This separate main process
claims queued sample rows, commits each outcome, backs off invalid/errors,
and stops at100jobs or90seconds, whichever comes first. Individual inference
attempts have60second hard alarms; interrupted jobs recover after120seconds.
A local flock prevents two CLI workers on the same machine. Queue/result
state stays in the database; rerun this command to continue remaining jobs.
No automatic recurring worker is installed by this script.

Usage: python scripts/refine_pending.py --account-id 1 --limit 25
Requires LLM_REFINE=1 plus a compatible private gateway URL/model.
"""
import argparse
import fcntl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.database import SessionLocal, engine, ensure_schema  # noqa: E402
from app.services.refine import refine_pending  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--account-id", type=int, required=True)
    parser.add_argument("--limit", type=int, default=25, help="max emails this run (1-100)")
    parser.add_argument("--wall-seconds", type=int, default=90, help="hard wall budget, max90s")
    args = parser.parse_args()
    lock = open("/tmp/mailpilot-ai-worker.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print({"busy": True})
        return 3

    ensure_schema(engine)
    db = SessionLocal()
    try:
        counts = refine_pending(db, args.account_id, limit=args.limit, queued_only=True,
                                sample_only=True, wall_seconds=args.wall_seconds)
    finally:
        db.close()
    print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
