from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

try:
    from atlas.eval.retrieval_eval import http_search_fn, load_golden_set, parse_cases, evaluate
except ModuleNotFoundError:
    # Support running from a source checkout.
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root / "src"))
    from atlas.eval.retrieval_eval import http_search_fn, load_golden_set, parse_cases, evaluate


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _log(msg: str) -> None:
    print(f"[{_now()}] {msg}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Project Atlas retrieval evaluation gate harness (vector-only)")
    ap.add_argument("--api-url", default=os.environ.get("ATLAS_API_URL", "http://127.0.0.1:18080"))
    ap.add_argument("--golden", required=True, help="Path to retrieval golden set JSON")
    ap.add_argument("--out", default="", help="Optional path to write report JSON")
    args = ap.parse_args()

    obj = load_golden_set(args.golden)
    cases, top_k, tenant_id, project_id = parse_cases(obj)

    _log(f"API: {args.api_url}")
    _log(f"Golden: {args.golden} ({len(cases)} cases)")
    _log(f"Scope: tenant_id={tenant_id or '(default)'} project_id={project_id or '(default)'}")

    search = http_search_fn(api_url=args.api_url, tenant_id=tenant_id, project_id=project_id, top_k=top_k)
    report = evaluate(cases=cases, search_fn=search, top_k=top_k)

    summary = report.get("summary") or {}
    _log(f"Hit rate@{top_k}: {summary.get('hit_rate'):.3f}")
    _log(f"MRR@{top_k}: {summary.get('mrr'):.3f}")

    if args.out:
        Path(args.out).write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
        _log(f"Wrote report: {args.out}")

    # Exit non-zero if any case failed.
    any_failed = any(not r.get("ok", False) for r in (report.get("results") or []))
    return 2 if any_failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
