from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILE = PROJECT_ROOT / "profiles" / "llamacpp-cuda-asr.json"


@dataclass(frozen=True)
class HarnessConfig:
    path: Path
    raw: dict[str, Any]

    @property
    def name(self) -> str:
        return str(self.raw.get("name", self.path.stem))

    def section(self, name: str) -> dict[str, Any]:
        value = self.raw.get(name, {})
        if isinstance(value, dict):
            return dict(value)
        return {}


def resolve_profile_path(value: str | None = None) -> Path:
    profile = value or os.environ.get("HARNESS_PROFILE")
    if not profile:
        return DEFAULT_PROFILE
    path = Path(profile)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def load_config(value: str | None = None) -> HarnessConfig:
    path = resolve_profile_path(value)
    with path.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return HarnessConfig(path=path, raw=raw)
