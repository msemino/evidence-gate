#!/usr/bin/env python3
"""
Gate test suite. Standard library only: `python tests/test_gate.py`.

It runs the gate in both directions on purpose:
  - the red fixture must trip all five checks and exit 1;
  - the green fixture must pass in silence and exit 0.

Without the second case you don't know whether the gate can tell a lie from an honest
page, or whether it simply bites everything put in front of it.
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES = ROOT / "tests" / "rules-fixture.toml"

RED = "tests/fixtures/lying-page.html"
GREEN = "tests/fixtures/honest-page.html"

# What the red fixture has to produce, per check.
EXPECTED_RED = {
    "denylist": "gaming GPU",
    "path-claim": "tests/test_robustness.py",
    "test-claim": "test_retries_on_upstream_timeout",
    "measured-claim": "coverage",
    "code-promise": "View the code",
}

failures: list[str] = []


def check(condition: bool, message: str) -> None:
    print(f"  {'ok  ' if condition else 'FAIL'}  {message}")
    if not condition:
        failures.append(message)


def run() -> tuple[int, list[dict]]:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "report.json"
        proc = subprocess.run(
            [sys.executable, "evidence_gate.py", "--rules", str(RULES),
             "--json", str(out), "--quiet"],
            cwd=ROOT, capture_output=True, text=True,
        )
        findings = json.loads(out.read_text(encoding="utf-8")) if out.exists() else []
    return proc.returncode, findings


def main() -> int:
    code, findings = run()
    red = [f for f in findings if f["page"] == RED]
    green = [f for f in findings if f["page"] == GREEN]

    print("\nRED fixture - every check must trip:")
    for name, fragment in EXPECTED_RED.items():
        hits = [f for f in red if f["check"] == name]
        check(bool(hits), f"{name} trips")
        if hits:
            check(any(fragment.lower() in f["match"].lower() for f in hits),
                  f"{name} points at '{fragment}'")

    print("\nRED fixture - severities:")
    for name in ("denylist", "path-claim", "test-claim", "code-promise"):
        check(any(f["check"] == name and f["severity"] == "FAIL" for f in red),
              f"{name} stops the build (FAIL)")
    check(all(f["severity"] == "WARN" for f in red if f["check"] == "measured-claim"),
          "measured-claim warns without stopping the build (WARN)")

    print("\nGREEN fixture - nothing may trip:")
    check(not green, f"zero findings (got {len(green)}: {[f['check'] for f in green]})")

    print("\nexit code:")
    check(code == 1, f"exit 1 when there is a FAIL (got {code})")

    print(f"\n{'-' * 60}")
    if failures:
        print(f"{len(failures)} check(s) failed:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("all green")
    return 0


if __name__ == "__main__":
    sys.exit(main())
