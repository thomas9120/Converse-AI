from __future__ import annotations

import importlib.util
import asyncio
import platform
import shutil
import socket
import sys
from dataclasses import dataclass
from typing import Any

import httpx

from conversational_harness.config import load_config
from conversational_harness.providers import build_providers


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def main() -> None:
    config = load_config()
    providers = build_providers(config)
    provider_statuses = asyncio.run(providers.check_statuses())
    checks = [
        check_python(),
        check_profile(config.path),
        check_package("fastapi"),
        check_package("uvicorn"),
        check_port_available(7860),
        check_cuda_tooling(),
        check_vulkan_tooling(),
        check_llamacpp(config.section("llm")),
    ]

    print(f"Profile: {config.name}")
    print(f"Profile path: {config.path}")
    print()
    for status in provider_statuses:
        state = "ready" if status["ready"] else "not ready"
        print(f"{status['kind'].upper()}: {status['name']} - {state} - {status['message']}")
    print()
    for check in checks:
        marker = "OK" if check.ok else "WARN"
        print(f"[{marker}] {check.name}: {check.detail}")

    if any(not check.ok for check in checks if check.name in {"Python", "Profile", "fastapi", "uvicorn"}):
        sys.exit(1)


def check_python() -> Check:
    version = sys.version_info
    ok = version >= (3, 11)
    return Check("Python", ok, platform.python_version())


def check_profile(path) -> Check:
    return Check("Profile", path.exists(), str(path))


def check_package(name: str) -> Check:
    found = importlib.util.find_spec(name) is not None
    return Check(name, found, "installed" if found else "missing")


def check_port_available(port: int) -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        result = sock.connect_ex(("127.0.0.1", port))
    if result == 0:
        return Check("Harness port", False, f"127.0.0.1:{port} is already in use; run stop.ps1/stop.sh if it is this harness")
    return Check("Harness port", True, f"127.0.0.1:{port} is available")


def check_cuda_tooling() -> Check:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return Check("CUDA tooling", False, "nvidia-smi not found; reduced CPU/mock mode can still run")
    return Check("CUDA tooling", True, nvidia_smi)


def check_vulkan_tooling() -> Check:
    vulkaninfo = shutil.which("vulkaninfo")
    if not vulkaninfo:
        return Check("Vulkan tooling", False, "vulkaninfo not found; Vulkan is optional and LLM-only")
    return Check("Vulkan tooling", True, vulkaninfo)


def check_llamacpp(llm_config: dict) -> Check:
    if llm_config.get("provider") != "llamacpp":
        return Check("llama.cpp server", True, "not selected by active profile")
    base_url = str(llm_config.get("base_url", "http://127.0.0.1:8080")).rstrip("/")
    health = fetch_json(f"{base_url}/health", timeout=1.5)
    if health is None:
        return Check("llama.cpp server", False, f"not reachable at {base_url}; start llama-server before using this profile")

    status = health.get("status")
    if status != "ok":
        message = health.get("error", {}).get("message", "server did not report ready")
        return Check("llama.cpp server", False, f"reachable at {base_url}, but not ready: {message}")

    models = fetch_json(f"{base_url}/v1/models", timeout=1.5)
    if models is None:
        return Check("llama.cpp server", False, f"health OK at {base_url}, but /v1/models failed")

    model_data = models.get("data", [])
    if not model_data:
        return Check("llama.cpp server", False, f"health OK at {base_url}, but no model is reported by /v1/models")

    model_ids = ", ".join(str(item.get("id", "unknown")) for item in model_data[:3])
    configured_model = str(llm_config.get("model", "auto"))
    loaded_ids = [str(item.get("id", "unknown")) for item in model_data]
    if configured_model != "auto" and configured_model not in loaded_ids:
        return Check(
            "llama.cpp server",
            False,
            f"ready at {base_url}, but profile model '{configured_model}' is not loaded; loaded model(s): {model_ids}",
        )
    return Check("llama.cpp server", True, f"ready at {base_url}; loaded model(s): {model_ids}")


def fetch_json(url: str, timeout: float) -> dict[str, Any] | None:
    try:
        response = httpx.get(url, timeout=timeout)
        response.raise_for_status()
        payload = response.json()
        if isinstance(payload, dict):
            return payload
    except Exception:
        return None
    return None


if __name__ == "__main__":
    main()
