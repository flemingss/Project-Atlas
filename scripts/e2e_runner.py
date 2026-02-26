from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time

import socket


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, text=True, capture_output=True, check=check)


def _print_proc_output(p: subprocess.CompletedProcess[str]) -> None:
    if p.stdout:
        print(p.stdout)
    if p.stderr:
        print(p.stderr, file=sys.stderr)


def _wait_for_health(api_url: str, timeout_s: float) -> None:
    import httpx

    deadline = time.time() + timeout_s
    with httpx.Client(timeout=5.0) as client:
        while time.time() < deadline:
            try:
                r = client.get(f"{api_url.rstrip('/')}/health")
                if r.status_code == 200:
                    return
            except Exception:
                pass
            time.sleep(0.25)
    raise RuntimeError(f"API not healthy within {timeout_s}s at {api_url}")


def _pick_free_port(preferred: int) -> int:
    # If preferred is free, use it.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", preferred))
            return preferred
        except OSError:
            pass

    # Otherwise pick an ephemeral free port.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s2:
        s2.bind(("127.0.0.1", 0))
        return int(s2.getsockname()[1])


def main() -> int:
    ap = argparse.ArgumentParser(description="Project Atlas full E2E runner")
    ap.add_argument("--api-url", default=os.environ.get("ATLAS_API_URL", "http://127.0.0.1:18080"))
    ap.add_argument("--api-port", type=int, default=int(os.environ.get("ATLAS_PORT", "8080")))
    ap.add_argument("--compose-file", default=os.environ.get("ATLAS_E2E_COMPOSE_FILE", "docker-compose.e2e.yml"))
    ap.add_argument(
        "--mode",
        choices=["deterministic", "local_llm"],
        default=os.environ.get("ATLAS_E2E_MODE", "deterministic"),
        help="Which scenario mode to run. deterministic is CI-safe; local_llm requires a reachable OpenAI-compatible server.",
    )
    ap.add_argument("--skip-docker", action="store_true")
    ap.add_argument("--skip-api", action="store_true")
    ap.add_argument("--leave-docker-up", action="store_true")
    ap.add_argument("--timeout", type=float, default=60.0, help="Health-check wait timeout in seconds.")
    args = ap.parse_args()

    api_url = args.api_url.rstrip("/")

    api_proc: subprocess.Popen[str] | None = None

    try:
        if not args.skip_docker:
            print(f"[e2e] docker compose up -d ({args.compose_file})")
            p = _run(["docker", "compose", "-f", args.compose_file, "up", "-d"], check=False)
            _print_proc_output(p)
            if p.returncode != 0:
                return p.returncode

        if not args.skip_api:
            print("[e2e] starting API: python -m atlas")

            chosen_port = _pick_free_port(args.api_port)
            if chosen_port != args.api_port:
                print(f"[e2e] port {args.api_port} busy; using {chosen_port}")

            api_url = f"http://127.0.0.1:{chosen_port}"
            env = os.environ.copy()
            env["ATLAS_HOST"] = "127.0.0.1"
            env["ATLAS_PORT"] = str(chosen_port)
            api_proc = subprocess.Popen(
                [sys.executable, "-m", "atlas"],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                env=env,
            )

        print(f"[e2e] waiting for API health at {api_url}...")
        _wait_for_health(api_url, timeout_s=args.timeout)

        print("[e2e] running scenarios...")
        scenarios = subprocess.run(
            [
                sys.executable,
                "scripts/e2e_scenarios.py",
                "--api-url",
                api_url,
                "--mode",
                args.mode,
            ],
            text=True,
        )
        return scenarios.returncode

    finally:
        if api_proc is not None:
            print("[e2e] stopping API...")
            # Windows: SIGINT is not supported for arbitrary processes.
            try:
                api_proc.terminate()
            except Exception:  # noqa: BLE001
                pass
            try:
                api_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                api_proc.kill()

            # Drain output for debugging
            if api_proc.stdout is not None:
                out = api_proc.stdout.read()
                if out:
                    print("[e2e] api output:\n" + out)

        if not args.skip_docker and not args.leave_docker_up:
            print(f"[e2e] docker compose down ({args.compose_file})")
            p = _run(["docker", "compose", "-f", args.compose_file, "down"], check=False)
            _print_proc_output(p)


if __name__ == "__main__":
    raise SystemExit(main())
