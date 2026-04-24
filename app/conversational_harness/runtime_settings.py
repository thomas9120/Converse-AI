from __future__ import annotations

import base64
import json
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from conversational_harness.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

SETTINGS_PATH = PROJECT_ROOT / "user_settings.json"

SAMPLER_KEYS = (
    "temperature",
    "top_k",
    "top_p",
    "min_p",
    "typical_p",
    "repeat_penalty",
    "frequency_penalty",
    "presence_penalty",
    "mirostat_tau",
    "mirostat_eta",
    "max_tokens",
)


@dataclass
class CharacterCard:
    name: str = ""
    description: str = ""
    personality: str = ""
    scenario: str = ""
    first_mes: str = ""
    mes_example: str = ""
    creator_notes: str = ""
    system_prompt: str = ""
    tags: list[str] = field(default_factory=list)
    creator: str = ""
    character_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "personality": self.personality,
            "scenario": self.scenario,
            "first_mes": self.first_mes,
            "mes_example": self.mes_example,
            "creator_notes": self.creator_notes,
            "system_prompt": self.system_prompt,
            "tags": self.tags,
            "creator": self.creator,
            "character_version": self.character_version,
        }

    def build_system_prompt(self, user_name: str, ai_name: str) -> str:
        parts: list[str] = []
        char_name = self.name or ai_name
        if self.description:
            parts.append(self.description)
        if self.personality:
            parts.append(f"Personality: {self.personality}")
        if self.scenario:
            parts.append(f"Scenario: {self.scenario}")
        if self.mes_example:
            example = self.mes_example.replace("{{user}}", user_name).replace("{{char}}", char_name)
            parts.append(f"Example dialogue:\n{example}")
        header = f"You are {char_name}."
        if user_name and user_name != "You":
            header += f" The user's name is {user_name}."
        if parts:
            return header + "\n\n" + "\n\n".join(parts)
        return header


def _parse_character_card(data: dict[str, Any]) -> CharacterCard:
    spec = data.get("data", data)
    return CharacterCard(
        name=str(spec.get("name", "")).strip(),
        description=str(spec.get("description", "")).strip(),
        personality=str(spec.get("personality", "")).strip(),
        scenario=str(spec.get("scenario", "")).strip(),
        first_mes=str(spec.get("first_mes", "")).strip(),
        mes_example=str(spec.get("mes_example", "")).strip(),
        creator_notes=str(spec.get("creator_notes", "")).strip(),
        system_prompt=str(spec.get("system_prompt", "")).strip(),
        tags=spec.get("tags", []) or [],
        creator=str(spec.get("creator", "")).strip(),
        character_version=str(spec.get("character_version", "")).strip(),
    )


def parse_character_json(text: str) -> CharacterCard:
    data = json.loads(text)
    return _parse_character_card(data)


def parse_character_png(data: bytes) -> CharacterCard:
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("Not a valid PNG file")
    offset = 8
    while offset < len(data):
        chunk_len = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + chunk_len]
        offset += 12 + chunk_len
        if chunk_type == b"tEXt":
            null_pos = chunk_data.index(0)
            keyword = chunk_data[:null_pos].decode("latin-1")
            if keyword == "chara":
                encoded = chunk_data[null_pos + 1 :].decode("latin-1")
                decoded = base64.b64decode(encoded).decode("utf-8")
                return parse_character_json(decoded)
        elif chunk_type == b"IEND":
            break
    raise ValueError("No character card data (tEXt chunk with key 'chara') found in PNG")


@dataclass
class RuntimeSettings:
    llm_overrides: dict[str, Any] = field(default_factory=dict)
    user_name: str = "You"
    ai_name: str = "Assistant"
    character: CharacterCard | None = None
    additional_system_prompt: str = ""
    profile_defaults: dict[str, Any] = field(default_factory=dict)

    def effective_sampler(self) -> dict[str, Any]:
        result = dict(self.profile_defaults)
        result.update({k: v for k, v in self.llm_overrides.items() if v is not None})
        return result

    def effective_system_prompt(self, manual_prompt: str = "") -> str:
        parts: list[str] = []
        if self.character:
            parts.append(self.character.build_system_prompt(self.user_name, self.ai_name))
        elif self.user_name != "You" or self.ai_name != "Assistant":
            header = f"You are {self.ai_name}."
            if self.user_name != "You":
                header += f" The user's name is {self.user_name}."
            parts.append(header)
        combined_supplement = (self.additional_system_prompt or "").strip()
        if manual_prompt:
            combined_supplement = f"{combined_supplement}\n\n{manual_prompt}".strip() if combined_supplement else manual_prompt
        if combined_supplement:
            parts.append(combined_supplement)
        return "\n\n".join(parts)

    def display_name_user(self) -> str:
        return self.user_name or "You"

    def display_name_ai(self) -> str:
        if self.character and self.character.name:
            return self.character.name
        return self.ai_name or "Assistant"

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "llm_overrides": dict(self.llm_overrides),
            "user_name": self.user_name,
            "ai_name": self.ai_name,
            "additional_system_prompt": self.additional_system_prompt,
        }
        if self.character:
            result["character"] = self.character.to_dict()
        return result

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)

    def apply_patch(self, patch: dict[str, Any]) -> None:
        if "llm_overrides" in patch:
            self.llm_overrides = {
                k: v for k, v in patch["llm_overrides"].items() if k in SAMPLER_KEYS
            }
        if "user_name" in patch:
            self.user_name = str(patch["user_name"]).strip() or "You"
        if "ai_name" in patch:
            self.ai_name = str(patch["ai_name"]).strip() or "Assistant"
        if "additional_system_prompt" in patch:
            self.additional_system_prompt = str(patch["additional_system_prompt"]).strip()

    def set_character(self, card: CharacterCard) -> None:
        self.character = card

    def clear_character(self) -> None:
        self.character = None


def load_runtime_settings(profile_llm_config: dict[str, Any] | None = None) -> RuntimeSettings:
    defaults: dict[str, Any] = {}
    if profile_llm_config:
        for key in SAMPLER_KEYS:
            if key in profile_llm_config:
                defaults[key] = profile_llm_config[key]
    settings = RuntimeSettings(profile_defaults=defaults)
    if SETTINGS_PATH.exists():
        try:
            with SETTINGS_PATH.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if "llm_overrides" in data:
                settings.llm_overrides = {
                    k: v for k, v in data["llm_overrides"].items() if k in SAMPLER_KEYS
                }
            if "user_name" in data:
                settings.user_name = str(data["user_name"]).strip() or "You"
            if "ai_name" in data:
                settings.ai_name = str(data["ai_name"]).strip() or "Assistant"
            if "additional_system_prompt" in data:
                settings.additional_system_prompt = str(data["additional_system_prompt"]).strip()
            if "character" in data and data["character"]:
                settings.character = _parse_character_card(data["character"])
        except Exception as exc:
            logger.warning("Failed to load user settings: %s", exc)
    return settings


def save_runtime_settings(settings: RuntimeSettings) -> None:
    try:
        with SETTINGS_PATH.open("w", encoding="utf-8") as handle:
            handle.write(settings.to_json())
    except Exception as exc:
        logger.warning("Failed to save user settings: %s", exc)
