from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from dataclasses import dataclass
from dataclasses import replace
from pathlib import Path
from typing import Any

from conversational_harness.config import DEFAULT_PROFILE, HarnessConfig, PROJECT_ROOT
from conversational_harness.providers.factory import build_tts, serialize_status


PRESET_PATH = PROJECT_ROOT / "profiles" / "tts-presets.json"


@dataclass(frozen=True)
class TTSPreset:
    id: str
    label: str
    description: str
    provider: str
    tts: dict[str, Any]
    turn: dict[str, Any]
    management: dict[str, Any]
    voices: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "provider": self.provider,
            "tts": deepcopy(self.tts),
            "turn": deepcopy(self.turn),
            "management": deepcopy(self.management),
            "voices": deepcopy(self.voices),
        }


def load_tts_presets(path: Path | None = None) -> list[TTSPreset]:
    source = path or PRESET_PATH
    with source.open("r", encoding="utf-8") as handle:
        raw = json.load(handle)
    presets = []
    for item in raw.get("presets", []):
        presets.append(
            TTSPreset(
                id=str(item["id"]),
                label=str(item.get("label", item["id"])),
                description=str(item.get("description", "")),
                provider=str(item.get("provider", item.get("tts", {}).get("provider", "mock"))),
                tts=dict(item.get("tts", {})),
                turn=dict(item.get("turn", {})),
                management=dict(item.get("management", {})),
                voices=list(item.get("voices", [])),
            )
        )
    if not presets:
        raise RuntimeError(f"No TTS presets found in {source}")
    return presets


class TTSRuntimeManager:
    def __init__(self, base_config: HarnessConfig, presets: list[TTSPreset]):
        self.base_config = base_config
        self.presets = presets
        self._lock = asyncio.Lock()
        self._selected_id = self._default_preset_id()
        self._voice_overrides: dict[str, str] = {}
        self._provider = build_tts(self.current_tts_config())

    @property
    def selected_preset(self) -> TTSPreset:
        return next(item for item in self.presets if item.id == self._selected_id)

    def get_provider(self):
        return self._provider

    def selected_voice(self) -> str | None:
        voice = self._voice_overrides.get(self._selected_id)
        if voice:
            return voice
        return self.selected_preset.tts.get("voice")

    def available_voices(self) -> list[dict[str, Any]]:
        return deepcopy(self.selected_preset.voices)

    def current_tts_config(self) -> dict[str, Any]:
        config = deepcopy(self.selected_preset.tts)
        voice = self.selected_voice()
        if voice is not None:
            config["voice"] = voice
        return config

    def current_turn_config(self) -> dict[str, Any]:
        merged = dict(self.base_config.section("turn"))
        merged.update(self.selected_preset.turn)
        return merged

    def merged_profile_raw(self) -> dict[str, Any]:
        raw = deepcopy(self.base_config.raw)
        raw["tts"] = self.current_tts_config()
        raw["turn"] = self.current_turn_config()
        return raw

    async def describe(self) -> dict[str, Any]:
        async with self._lock:
            status = await self._provider.check_status()
            selected = replace(status, selected=True)
            selected_preset = self.selected_preset.to_dict()
            selected_preset["tts"] = self.current_tts_config()
            return {
                "selected_preset_id": self._selected_id,
                "selected_preset": selected_preset,
                "presets": [item.to_dict() for item in self.presets],
                "available_voices": self.available_voices(),
                "selected_voice": self.selected_voice(),
                "status": serialize_status(selected),
            }

    async def select_preset(self, preset_id: str) -> dict[str, Any]:
        async with self._lock:
            self._selected_id = self._require_preset(preset_id).id
            self._provider = build_tts(self.current_tts_config())
        return await self.describe()

    async def select_voice(self, voice_id: str) -> dict[str, Any]:
        async with self._lock:
            normalized = str(voice_id).strip()
            if not normalized:
                raise KeyError("Unknown TTS voice: ")
            allowed = {str(item.get("id")) for item in self.selected_preset.voices if item.get("id")}
            if allowed and normalized not in allowed:
                raise KeyError(f"Unknown TTS voice: {voice_id}")
            self._voice_overrides[self._selected_id] = normalized
            self._provider = build_tts(self.current_tts_config())
        return await self.describe()

    async def load_selected(self) -> dict[str, Any]:
        async with self._lock:
            await self._provider.load()
        return await self.describe()

    async def unload_selected(self) -> dict[str, Any]:
        async with self._lock:
            await self._provider.unload()
        return await self.describe()

    def _require_preset(self, preset_id: str) -> TTSPreset:
        for preset in self.presets:
            if preset.id == preset_id:
                return preset
        raise KeyError(f"Unknown TTS preset: {preset_id}")

    def _default_preset_id(self) -> str:
        tts = self.base_config.section("tts")
        provider = str(tts.get("provider", ""))
        voice = tts.get("voice")
        model = tts.get("model")
        for preset in self.presets:
            if preset.tts.get("provider") != provider:
                continue
            if voice is not None and preset.tts.get("voice") != voice:
                continue
            if model is not None and preset.tts.get("model") != model:
                continue
            return preset.id
        return self.presets[0].id


def build_default_tts_runtime(base_config: HarnessConfig | None = None) -> TTSRuntimeManager:
    config = base_config
    if config is None:
        default_path = DEFAULT_PROFILE
        with default_path.open("r", encoding="utf-8") as handle:
            raw = json.load(handle)
        config = HarnessConfig(path=default_path, raw=raw)
    return TTSRuntimeManager(config, load_tts_presets())
