#!/usr/bin/env python3
"""
Refresh the bundled manufacturer list from the IEEE. Run before a release.
"""

import csv
import io
import sys
import urllib.request
from pathlib import Path

URL = "https://standards-oui.ieee.org/oui/oui.csv"
DEST = Path(__file__).resolve().parent.parent / "src" / "pingthing" / "data" / "oui.csv"
EXPECTED_HEADER = ["Registry", "Assignment", "Organization Name", "Organization Address"]


def main():
    # the IEEE server rejects requests without a browser-ish user agent
    request = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (pingthing update script)"})
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8")

    rows = list(csv.reader(io.StringIO(text)))
    if rows[0] != EXPECTED_HEADER:
        sys.exit(f"Unexpected header, format may have changed: {rows[0]}")
    if len(rows) < 20000:
        sys.exit(f"Only {len(rows)} rows, refusing to replace the bundled list")

    DEST.write_text(text, encoding="utf-8", newline="")
    print(f"Wrote {len(rows) - 1} entries to {DEST}")


if __name__ == "__main__":
    main()
