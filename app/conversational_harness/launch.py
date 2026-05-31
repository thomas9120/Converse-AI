from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import webbrowser
from pathlib import Path
from typing import Sequence

import httpx

from conversational_harness.config import PROJECT_ROOT, load_config, resolve_profile_path
from conversational_harness.doctor import (
    FATAL_CHECKS,
    Check,
    check_cuda_tooling,
    check_llamacpp,
    check_package,
    check_port_available,
    check_profile,
    check_provider_statuses,
    check_python,
    check_vulkan_tooling,
    collect_checks,
)
from conversational_harness.providers import build_providers


DEFAULT_PORT = 7860


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    profiles = list_profiles()
    if args.list_profiles:
        print_profiles(profiles)
        return 0

    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.no_prompt
    profile_path = choose_profile(args.profile, profiles, interactive)
    port = int(args.port or os.environ.get("HARNESS_PORT") or DEFAULT_PORT)

    try:
        config = load_config(str(profile_path))
    except Exception as exc:
        print(f"[ERR] Profile: {exc}", file=sys.stderr)
        return 1

    print("Conversational AI Harness")
    print(f"  Profile: {config.name}")
    print(f"  Path:    {relative_display(config.path)}")
    print(f"  URL:     http://127.0.0.1:{port}")
    print()

    if not args.skip_checks:
        checks = run_launch_checks(config, port, skip_port_check=args.skip_port_check)
        print_checks(checks)
        if any(not check.ok for check in checks if check.name in FATAL_CHECKS):
            print()
            print("Startup stopped because a required check failed.", file=sys.stderr)
            return 1
        print()

    env = os.environ.copy()
    env["HARNESS_PROFILE"] = str(config.path)
    env["HARNESS_PORT"] = str(port)
    env["PYTHONPATH"] = str(PROJECT_ROOT / "app")

    process = start_server(port, env)
    url = f"http://127.0.0.1:{port}"
    try:
        if wait_for_server(url):
            print(f"Server is ready: {url}")
            maybe_open_browser(url, args, interactive)
        else:
            print("Server did not report ready before the timeout. Leaving process in foreground.")
        return wait_for_process(process)
    except KeyboardInterrupt:
        print()
        print("Stopping harness server...")
        stop_process(process)
        return 130


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Start the Conversational AI Harness.")
    parser.add_argument("--profile", help="Profile JSON path to use.")
    parser.add_argument("--port", type=int, help=f"HTTP port to bind. Defaults to HARNESS_PORT or {DEFAULT_PORT}.")
    parser.add_argument("--list-profiles", action="store_true", help="List available profiles and exit.")
    parser.add_argument("--no-browser", action="store_true", help="Do not open the browser.")
    parser.add_argument("--open-browser", action="store_true", help="Open the browser after the server is ready.")
    parser.add_argument("--no-prompt", action="store_true", help="Disable interactive prompts.")
    parser.add_argument("--skip-checks", action="store_true", help="Skip preflight checks.")
    parser.add_argument("--skip-port-check", action="store_true", help="Skip the preflight port availability check.")
    return parser.parse_args(argv)


def list_profiles() -> list[Path]:
    return sorted(profile for profile in (PROJECT_ROOT / "profiles").glob("*.json") if is_harness_profile(profile))


def is_harness_profile(path: Path) -> bool:
    try:
        with path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except Exception:
        return False
    return isinstance(raw, dict) and any(key in raw for key in ("vad", "asr", "llm", "tts"))


def print_profiles(profiles: Sequence[Path]) -> None:
    for profile in profiles:
        print(relative_display(profile))


def choose_profile(explicit: str | None, profiles: Sequence[Path], interactive: bool) -> Path:
    env_profile = os.environ.get("HARNESS_PROFILE")
    if explicit or env_profile or not interactive:
        return resolve_profile_path(explicit)

    default = resolve_profile_path(None)
    print("Available profiles:")
    for index, profile in enumerate(profiles, start=1):
        marker = " (default)" if profile == default else ""
        print(f"  {index}. {profile.name}{marker}")
    answer = input("Choose profile number, or press Enter for default: ").strip()
    print()
    if not answer:
        return default
    try:
        selected = profiles[int(answer) - 1]
    except (ValueError, IndexError):
        print("Invalid selection; using default profile.")
        return default
    return selected


def run_launch_checks(config, port: int, *, skip_port_check: bool = False) -> list[Check]:
    providers = build_providers(config)
    provider_statuses = asyncio.run(check_provider_statuses(providers))
    checks = collect_checks(
        [
            ("Python", check_python),
            ("Profile", lambda: check_profile(config.path)),
            ("fastapi", lambda: check_package("fastapi")),
            ("uvicorn", lambda: check_package("uvicorn")),
            *([] if skip_port_check else [("Harness port", lambda: check_port_available(port))]),
            ("CUDA tooling", check_cuda_tooling),
            ("Vulkan tooling", check_vulkan_tooling),
            ("llama.cpp server", lambda: check_llamacpp(config.section("llm"))),
        ]
    )
    for status in provider_statuses:
        state = "ready" if status["ready"] else "not ready"
        checks.append(Check(f"{status['kind'].upper()} provider", bool(status["ready"]), f"{status['name']} - {state} - {status['message']}"))
    return checks


def print_checks(checks: Sequence[Check]) -> None:
    for check in checks:
        if check.ok:
            marker = "OK"
        elif check.name in FATAL_CHECKS:
            marker = "ERR"
        else:
            marker = "WARN"
        print(f"[{marker}] {check.name}: {check.detail}")


def start_server(port: int, env: dict[str, str]) -> subprocess.Popen:
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "conversational_harness.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
    ]
    print("Starting server...")
    return subprocess.Popen(command, cwd=PROJECT_ROOT, env=env)


def wait_for_server(url: str, *, timeout_s: float = 20.0) -> bool:
    deadline = time.monotonic() + timeout_s
    status_url = f"{url}/api/status"
    while time.monotonic() < deadline:
        try:
            response = httpx.get(status_url, timeout=0.5)
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def maybe_open_browser(url: str, args: argparse.Namespace, interactive: bool) -> None:
    if args.no_browser:
        return
    should_open = args.open_browser
    if interactive and not should_open:
        answer = input("Open in browser? [Y/n] ").strip().lower()
        should_open = answer in ("", "y", "yes")
    if should_open:
        webbrowser.open(url)


def wait_for_process(process: subprocess.Popen) -> int:
    return int(process.wait())


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def relative_display(path: Path) -> str:
    try:
        return path.relative_to(PROJECT_ROOT).as_posix()
    except ValueError:
        return str(path)


if __name__ == "__main__":
    raise SystemExit(main())
