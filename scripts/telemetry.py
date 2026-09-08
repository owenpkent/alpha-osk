"""Print the opt-in telemetry aggregate: current totals plus the history.

Usage:
    python scripts/telemetry.py           # totals, and the last 14 days
    python scripts/telemetry.py --days 60 # a longer window
    python scripts/telemetry.py --json    # raw, for piping

Reads two public sources and needs no credentials, which is the point:
every number here is one anybody can check.

  * ``/v1/aggregate`` on the worker, which is a SUM over the latest row per
    install and knows only *now*.  Opting out deletes a row, so these totals
    can go down as well as up.
  * the dated series committed by ``.github/workflows/telemetry-aggregate.yml``
    to the ``telemetry-data`` branch, which is the only record of what the
    totals were on any earlier day.  That workflow exists precisely because
    the endpoint keeps no history.

Neither source carries anything about an individual: no content, no IP, no
hostname.  For per-install rows (say, how many installs are on which version)
use the D1 console or:

    cd backend/cf-worker
    npx wrangler d1 execute alpha-osk-telemetry --remote --command "SELECT * FROM users"
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import sys
import urllib.error
import urllib.request

# Read from the shipped constant rather than a copy, so this script cannot
# drift from the endpoint the keyboard actually submits to.
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent))
from src.telemetry import DEFAULT_ENDPOINT  # noqa: E402

SERIES_URL = (
    "https://raw.githubusercontent.com/owenpkent/alpha-osk/"
    "telemetry-data/docs/research/data/telemetry-aggregate.csv"
)
TIMEOUT = 20

# Label, key, and whether to render as an integer.
FIELDS = [
    ("Installs sharing", "users", True),
    ("Keystrokes typed", "keystrokes", True),
    ("Words", "words", True),
    ("Predictions accepted", "predictions", True),
    ("Keystrokes saved", "keystrokes_saved", True),
    ("Suggestions offered", "prediction_offers", True),
    ("Sessions", "sessions", True),
    ("Minutes", "minutes", False),
]


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Alpha-OSK-tools"})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
        return resp.read().decode("utf-8")


def _fetch_totals() -> dict:
    return json.loads(_get(f"{DEFAULT_ENDPOINT.rstrip('/')}/v1/aggregate"))


def _fetch_series() -> list[dict]:
    try:
        return list(csv.DictReader(io.StringIO(_get(SERIES_URL))))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []  # the workflow has not written a row yet
        raise


def _num(row: dict, key: str) -> float:
    try:
        return float(row.get(key) or 0)
    except ValueError:
        return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--days", type=int, default=14, help="rows of history to show (default 14)")
    ap.add_argument("--json", action="store_true", help="print raw JSON and exit")
    args = ap.parse_args()

    if not DEFAULT_ENDPOINT:
        print(
            "DEFAULT_ENDPOINT is empty in src/telemetry.py, so this build sends\n"
            "nothing and there is nothing to read. See backend/cf-worker/README.md.",
            file=sys.stderr,
        )
        return 1

    try:
        totals = _fetch_totals()
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        print(f"could not read {DEFAULT_ENDPOINT}: {e}", file=sys.stderr)
        return 1

    try:
        series = _fetch_series()
    except (urllib.error.URLError, OSError) as e:
        print(f"warning: could not read the history: {e}", file=sys.stderr)
        series = []

    if args.json:
        print(json.dumps({"totals": totals, "series": series}, indent=2))
        return 0

    print(f"Alpha-OSK telemetry  ({DEFAULT_ENDPOINT})")
    print("=" * 52)
    for label, key, as_int in FIELDS:
        v = _num(totals, key)
        print(f"  {label:<22}{int(v):>14,}" if as_int else f"  {label:<22}{v:>14,.1f}")

    typed = _num(totals, "keystrokes")
    saved = _num(totals, "keystrokes_saved")
    offers = _num(totals, "prediction_offers")
    accepted = _num(totals, "predictions")
    print("-" * 52)
    if typed:
        # Savings against what typing them all by hand would have cost.
        print(f"  {'Effort saved':<22}{saved / (typed + saved) * 100:>13.1f}%")
    if offers:
        print(f"  {'Suggestions accepted':<22}{accepted / offers * 100:>13.1f}%")

    if not series:
        print("\nNo history yet. The daily workflow writes the first row at 04:10 UTC;")
        print("run it now with: gh workflow run telemetry-aggregate.yml")
        return 0

    print(f"\nHistory (last {args.days} of {len(series)} days)")
    print("-" * 52)
    print(f"  {'date':<12}{'installs':>10}{'keystrokes':>14}{'saved':>14}")
    for row in series[-args.days :]:
        # jq's @csv quotes the date, and a backslash cannot appear inside an
        # f-string expression before Python 3.12, so strip it out here.
        date = (row.get("date") or "").strip('"')
        print(
            f"  {date:<12}"
            f"{int(_num(row, 'users')):>10,}"
            f"{int(_num(row, 'keystrokes')):>14,}"
            f"{int(_num(row, 'keystrokes_saved')):>14,}"
        )
    print(f"\n  full series: {SERIES_URL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
