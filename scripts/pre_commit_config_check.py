#!/usr/bin/env python3
"""Pre-commit hook: ensure config files match stock .example templates.

Usage (manual):
    python scripts/pre_commit_config_check.py

Usage (git hook — add to .git/hooks/pre-commit):
    #!/bin/sh
    python scripts/pre_commit_config_check.py || exit 1

The script exits 0 (pass) if:
  - No live config files are staged (nothing to check).
  - Live files match their .example counterparts exactly.

Exits 1 (fail) if a staged config file differs from stock.

If the live files don't exist yet (fresh clone), the script passes —
it only flags *divergent* files that are about to be committed.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"

GUARDED_FILES = [
    ("pipeline.yaml", "pipeline.yaml.example"),
    ("models.yaml", "models.yaml.example"),
]


def _staged_files() -> set[str]:
    """Return the set of file paths currently staged in the git index."""
    try:
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            capture_output=True,
            text=True,
            check=True,
        )
        return set(result.stdout.strip().splitlines())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()


def main() -> int:
    staged = _staged_files()
    failures: list[str] = []

    for live_name, example_name in GUARDED_FILES:
        # Only check if the live file is staged for commit.
        relative = f"config/{live_name}"
        if relative not in staged:
            continue

        live_path = CONFIG_DIR / live_name
        example_path = CONFIG_DIR / example_name

        if not live_path.exists():
            continue  # Nothing to compare.
        if not example_path.exists():
            failures.append(
                f"  {relative} is staged but {example_name} is missing "
                f"(cannot verify stock match)"
            )
            continue

        live_content = live_path.read_text(encoding="utf-8")
        stock_content = example_path.read_text(encoding="utf-8")

        if live_content != stock_content:
            failures.append(
                f"  {relative} differs from {example_name}.\n"
                f"    This file contains operator-local configuration and should "
                f"not be committed.\n"
                f"    To fix: git reset HEAD {relative}\n"
                f"    To restore stock: copy {example_name} → {live_name}"
            )

    if failures:
        print("🛑 Config commit guardrail — blocked staged config files:\n")
        print("\n".join(failures))
        print(
            "\nConfig files (pipeline.yaml, models.yaml) are operator-local and "
            "should not be committed. The .example copies are the stock reference.\n"
            "If you intentionally changed the stock defaults, update the .example "
            "file instead and commit that."
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
