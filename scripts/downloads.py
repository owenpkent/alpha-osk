"""Print download counts for every Alpha-OSK release.

Usage:
    python scripts/downloads.py

Requires the `gh` CLI authenticated against an account with read access to
okstudio1/alpha-osk-releases.
"""
from __future__ import annotations

import json
import subprocess
import sys

REPO = "okstudio1/alpha-osk-releases"


def main() -> int:
    try:
        out = subprocess.run(
            ["gh", "api", f"repos/{REPO}/releases", "--paginate"],
            capture_output=True, text=True, check=True,
        ).stdout
    except FileNotFoundError:
        print("gh CLI not found. Install from https://cli.github.com/", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print(f"gh api failed: {e.stderr}", file=sys.stderr)
        return 1

    releases = json.loads(out)
    total = 0
    print(f"{'tag':<10} {'published':<12} {'downloads':>10}")
    print("-" * 34)
    for r in releases:
        count = sum(a["download_count"] for a in r["assets"])
        total += count
        published = r["published_at"][:10] if r.get("published_at") else "-"
        print(f"{r['tag_name']:<10} {published:<12} {count:>10}")
    print("-" * 34)
    print(f"{'TOTAL':<23} {total:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
