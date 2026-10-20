#!/usr/bin/env python3
"""docs/TRACKING.md stays honest (ROADMAP §8): every status is one of the four states, and
every IMPLEMENTED_TESTED row names a test file that exists and that the Makefile runs.
    python3 scripts/check-tracking.py
"""
import fnmatch
import os
import re
import sys

ROOT = os.path.realpath(os.path.join(os.path.dirname(__file__), ".."))
STATES = {"IMPLEMENTED_TESTED", "IMPLEMENTED_UNTESTED", "BLOCKED_HOST", "NOT_IMPLEMENTED"}


def main():
    make = open(os.path.join(ROOT, "Makefile")).read()
    # a Makefile loop over a glob (`for t in scripts/test/mission-*.test.py`) runs every match
    globs = re.findall(r"(scripts/test/[^\s;\"']*\*[^\s;\"']*)", make)
    bad, rows = [], 0
    for ln in open(os.path.join(ROOT, "docs", "TRACKING.md")):
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if not ln.startswith("|") or len(cells) != 4 or cells[0] in ("§", "") or set(cells[0]) <= set("-"):
            continue
        rows += 1
        sec, req, status, ev = cells
        if status not in STATES:
            bad.append("%s %s: status %r is not one of %s" % (sec, req, status, ", ".join(sorted(STATES))))
            continue
        if status == "IMPLEMENTED_TESTED":
            tests = re.findall(r"`(scripts/test/[^`]+)`", ev)
            if not tests:
                bad.append("%s %s: IMPLEMENTED_TESTED needs a scripts/test/ file as evidence" % (sec, req))
            for t in tests:
                if not os.path.isfile(os.path.join(ROOT, t)):
                    bad.append("%s %s: %s does not exist" % (sec, req, t))
                elif t not in make and not any(fnmatch.fnmatch(t, g) for g in globs):
                    bad.append("%s %s: %s is not run by the Makefile" % (sec, req, t))
    for b in bad:
        print("BLOCK[tracking]: %s" % b, file=sys.stderr)
    if not bad:
        print("tracking ok: %d requirements" % rows)
    return 2 if bad or not rows else 0


if __name__ == "__main__":
    sys.exit(main())
