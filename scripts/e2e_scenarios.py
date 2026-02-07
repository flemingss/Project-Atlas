from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

try:
    from atlas.e2e.scenarios import run_scenarios
except ModuleNotFoundError:
    # Support running the script directly from a source checkout without
    # requiring `pip install -e .`.
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))
    from atlas.e2e.scenarios import run_scenarios


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Project Atlas E2E scenario runner")
    ap.add_argument("--api-url", default=os.environ.get("ATLAS_API_URL", "http://127.0.0.1:18080"))
    ap.add_argument("--qdrant-url", default=os.environ.get("ATLAS_QDRANT_URL", "http://localhost:6333"))
    ap.add_argument("--collection", default=os.environ.get("ATLAS_QDRANT_COLLECTION", "atlas_chunks"))
    ap.add_argument("--timeout", type=float, default=20.0)
    args = ap.parse_args()

    _log(f"API: {args.api_url}")
    _log(f"Qdrant: {args.qdrant_url}")

    summary = run_scenarios(
        api_url=args.api_url,
        qdrant_url=args.qdrant_url,
        collection=args.collection,
        timeout_s=float(args.timeout),
    )
    if summary.ok:
        _log("E2E scenarios: PASS")
        return 0

    for r in summary.results:
        if not r.ok:
            _log(f"FAIL {r.name}: {r.detail}")
    _log("E2E scenarios: FAIL")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
