#!/usr/bin/env python
"""Check that every part of Invento is running AND connected to the others, and say how to fix what isn't.

    python doctor.py
"""

from __future__ import annotations

import sys

from app import devdoctor as dd
from app import devserver as ds

def main() -> int:
    ds.tolerate_any_output(sys.stdout)
    tty = sys.stdout.isatty()
    paint = (lambda n, s: f"\x1b[{n}m{s}\x1b[0m") if tty else (lambda _n, s: s)
    fancy = ds.can_print("✔✖·")  # old Windows code pages can't show these; fall back to plain words
    marks = {
        dd.PASS: paint(32, "✔" if fancy else "ok  "),
        dd.INFO: paint(36, "·" if fancy else "-   "),
        dd.WARN: paint(33, "!" if fancy else "warn"),
        dd.FAIL: paint(31, "✖" if fancy else "FAIL"),
    }

    print(paint(1, "Invento doctor"))
    checks = dd.run_checks(dd.Settings.load(), dd.RealProbe())
    for c in checks:
        print(f"  {marks[c.level]} {c.message}")
        if c.fix and c.level in (dd.WARN, dd.FAIL):
            print(f"      {paint(33, 'fix:')} {c.fix}")

    failures, warnings = dd.summarize(checks)
    tail = paint(31, f"{failures} problem{'s' if failures != 1 else ''} to fix") if failures else paint(32, "Everything is connected")
    if warnings:
        tail += paint(33, f", {warnings} warning{'s' if warnings != 1 else ''}")
    print(f"\n{tail}.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
