"""Run Phase 2 health checks once or continuously at a fixed interval."""
import argparse
import time
from datetime import datetime, timezone

from eval_recall import main as run_health_check


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--interval",
        type=int,
        help="seconds between checks; omit to run exactly once",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    while True:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        result = run_health_check(note=f"scheduled_{timestamp}")
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
