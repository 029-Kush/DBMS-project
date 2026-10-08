"""Run Phase 2 health checks once or continuously at a fixed interval."""
import argparse
import time

import psycopg2
from datetime import datetime, timezone

from eval_recall import main as run_health_check
from heal import heal_once


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interval",
        type=int,
        help="seconds between checks; omit to run exactly once",
    )
    parser.add_argument(
        "--heal",
        action="store_true",
        help="diagnose and repair supported faults (default: observe only)",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    while True:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        if args.heal:
            outcome = heal_once(note=f"scheduled_{timestamp}")
            result = outcome["after"] or outcome["before"]
            print(
                f"Heal outcome: {outcome['outcome']}"
                + (f" actions={list(outcome['actions'])}" if outcome["actions"] else "")
                + (f" recovered_events={outcome['recovered']}" if outcome["recovered"] else "")
                + (f" error={outcome['error'][:80]}" if outcome.get("error") else "")
            )
            if result is None:  # database unavailable or another healer is active
                result = {"status": "UNKNOWN", "issues": []}
        else:
            try:
                result = run_health_check(note=f"scheduled_{timestamp}")
            except psycopg2.OperationalError as exc:
                if args.interval is None:
                    raise
                print(f"Database unavailable ({str(exc).strip()[:80]}); will retry.")
                result = {"status": "UNKNOWN", "issues": []}
        print(
            f"Monitor result: {result['status']} "
            f"({', '.join(result['issues']) if result['issues'] else 'no issues'})"
        )
        if args.interval is None:
            return
        print(f"Next check in {args.interval} seconds. Press Ctrl+C to stop.")
        try:
            time.sleep(args.interval)
        except KeyboardInterrupt:
            print("Monitor stopped.")
            return


if __name__ == "__main__":
    main()
