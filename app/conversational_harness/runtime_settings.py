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
MEMORY_PATH = PROJECT_ROOT / "memory.md"
MAX_MEMORY_CHARS = 20000
MODE_CHAT = "chat"
MODE_COMPANION = "companion"

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

VALID_MODES = {MODE_CHAT, MODE_COMPANION}


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

    def first_message(self, user_name: str, ai_name: str) -> str:
        char_name = self.name or ai_name
        return self.first_mes.replace("{{user}}", user_name).replace("{{char}}", char_name).strip()


@dataclass
class CompanionSettings:
    user_name: str = "You"
    ai_name: str = "Companion"
    system_prompt: str = ""
    llm_overrides: dict[str, Any] = field(default_factory=dict)
    memory_enabled: bool = True

    def effective_sampler(
        self, profile_defaults: dict[str, Any], server_defaults: dict[str, Any]
    ) -> dict[str, Any]:
        result = dict(server_defaults)
        result.update({k: v for k, v in profile_defaults.items() if v is not None})
        result.update({k: v for k, v in self.llm_overrides.items() if v is not None})
        return result

    def sampler_display(
        self, profile_defaults: dict[str, Any], server_defaults: dict[str, Any]
    ) -> dict[str, Any]:
        display = {}
        for key in SAMPLER_KEYS:
            if key in self.llm_overrides and self.llm_overrides[key] is not None:
                display[key] = self.llm_overrides[key]
            elif key in profile_defaults and profile_defaults[key] is not None:
                display[key] = profile_defaults[key]
            elif key in server_defaults and server_defaults[key] is not None:
                display[key] = server_defaults[key]
        return display

    def to_dict(
        self, profile_defaults: dict[str, Any], server_defaults: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "user_name": self.user_name,
            "ai_name": self.ai_name,
            "system_prompt": self.system_prompt,
            "llm_overrides": dict(self.llm_overrides),
            "memory_enabled": self.memory_enabled,
            "sampler_display": self.sampler_display(profile_defaults, server_defaults),
        }

    def apply_patch(self, patch: dict[str, Any]) -> None:
        if "llm_overrides" in patch and isinstance(patch["llm_overrides"], dict):
            self.llm_overrides = {
                k: v for k, v in patch["llm_overrides"].items() if k in SAMPLER_KEYS
            }
        if "user_name" in patch:
            self.user_name = str(patch["user_name"]).strip() or "You"
        if "ai_name" in patch:
            self.ai_name = str(patch["ai_name"]).strip() or "Companion"
        if "system_prompt" in patch:
            self.system_prompt = str(patch["system_prompt"]).strip()
        if "memory_enabled" in patch:
            self.memory_enabled = bool(patch["memory_enabled"])


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
    active_mode: str = MODE_CHAT
    companion: CompanionSettings = field(default_factory=CompanionSettings)
    profile_defaults: dict[str, Any] = field(default_factory=dict)
    server_defaults: dict[str, Any] = field(default_factory=dict)

    def effective_sampler(self, mode: str | None = None) -> dict[str, Any]:
        if self._normalize_mode(mode or self.active_mode) == MODE_COMPANION:
            return self.companion.effective_sampler(self.profile_defaults, self.server_defaults)
        result = dict(self.server_defaults)
        result.update({k: v for k, v in self.profile_defaults.items() if v is not None})
        result.update({k: v for k, v in self.llm_overrides.items() if v is not None})
        return result

    def sampler_display(self) -> dict[str, Any]:
        display = {}
        for key in SAMPLER_KEYS:
            if key in self.llm_overrides and self.llm_overrides[key] is not None:
                display[key] = self.llm_overrides[key]
            elif key in self.profile_defaults and self.profile_defaults[key] is not None:
                display[key] = self.profile_defaults[key]
            elif key in self.server_defaults and self.server_defaults[key] is not None:
                display[key] = self.server_defaults[key]
        return display

    def set_server_defaults(self, params: dict[str, Any]) -> None:
        mapping = {
            "temperature": "temperature",
            "top_k": "top_k",
            "top_p": "top_p",
            "min_p": "min_p",
            "typical_p": "typical_p",
            "repeat_penalty": "repeat_penalty",
            "frequency_penalty": "frequency_penalty",
            "presence_penalty": "presence_penalty",
            "mirostat_tau": "mirostat_tau",
            "mirostat_eta": "mirostat_eta",
            "max_tokens": "n_predict",
        }
        self.server_defaults = {}
        for target, source in mapping.items():
            if source in params and params[source] is not None:
                self.server_defaults[target] = params[source]
        if "max_tokens" in self.server_defaults and self.server_defaults["max_tokens"] == -1:
            del self.server_defaults["max_tokens"]

    def effective_system_prompt(
        self, manual_prompt: str = "", mode: str | None = None, memory_text: str = ""
    ) -> str:
        if self._normalize_mode(mode or self.active_mode) == MODE_COMPANION:
            return self.companion_system_prompt(manual_prompt, memory_text)
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

    def companion_system_prompt(self, manual_prompt: str = "", memory_text: str = "") -> str:
        parts: list[str] = []
        header = f"You are {self.companion.ai_name or 'Companion'}."
        if self.companion.user_name and self.companion.user_name != "You":
            header += f" The user's name is {self.companion.user_name}."
        parts.append(header)
        if self.companion.system_prompt:
            parts.append(self.companion.system_prompt)
        if self.companion.memory_enabled and memory_text.strip():
            parts.append("Long-term memory:\n" + memory_text.strip())
        if manual_prompt.strip():
            parts.append(manual_prompt.strip())
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
            "active_mode": self.active_mode,
            "sampler_display": self.sampler_display(),
            "companion": self.companion.to_dict(self.profile_defaults, self.server_defaults),
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
        if "active_mode" in patch:
            self.active_mode = self._normalize_mode(str(patch["active_mode"]))
        if "user_name" in patch:
            self.user_name = str(patch["user_name"]).strip() or "You"
        if "ai_name" in patch:
            self.ai_name = str(patch["ai_name"]).strip() or "Assistant"
        if "additional_system_prompt" in patch:
            self.additional_system_prompt = str(patch["additional_system_prompt"]).strip()
        if "companion" in patch and isinstance(patch["companion"], dict):
            self.companion.apply_patch(patch["companion"])

    def set_character(self, card: CharacterCard) -> None:
        self.character = card

    def clear_character(self) -> None:
        self.character = None

    def _normalize_mode(self, mode: str) -> str:
        return mode if mode in VALID_MODES else MODE_CHAT


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
            if "active_mode" in data:
                settings.active_mode = settings._normalize_mode(str(data["active_mode"]))
            if "companion" in data and isinstance(data["companion"], dict):
                settings.companion.apply_patch(data["companion"])
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


class MemoryStore:
    def __init__(self, path: Path = MEMORY_PATH, max_chars: int = MAX_MEMORY_CHARS):
        self.path = path
        self.max_chars = max_chars

    def read(self) -> str:
        if not self.path.exists():
            return ""
        text = self.path.read_text(encoding="utf-8")
        if len(text) > self.max_chars:
            return text[-self.max_chars :]
        return text

    def metadata(self) -> dict[str, Any]:
        exists = self.path.exists()
        text = self.read() if exists else ""
        modified = self.path.stat().st_mtime if exists else None
        return {
            "path": str(self.path),
            "exists": exists,
            "chars": len(text),
            "max_chars": self.max_chars,
            "modified": modified,
        }

    def payload(self) -> dict[str, Any]:
        return {"text": self.read(), "metadata": self.metadata()}

    def write(self, text: str) -> None:
        clean = text.strip()
        if len(clean) > self.max_chars:
            raise ValueError(f"Memory is too large. Keep it under {self.max_chars} characters.")
        if clean:
            self.path.write_text(clean + "\n", encoding="utf-8")
        elif self.path.exists():
            self.path.unlink()

    def append_summary(self, summary: str, title: str) -> str:
        clean_summary = summary.strip()
        if not clean_summary:
            raise ValueError("Memory summary was empty.")
        existing = self.read().strip()
        section = f"## {title}\n\n{clean_summary}"
        combined = f"{existing}\n\n{section}".strip() if existing else section
        if len(combined) > self.max_chars:
            overflow = len(combined) - self.max_chars
            combined = combined[overflow:].lstrip()
        self.write(combined)
        return combined

    def clear(self) -> None:
        if self.path.exists():
            self.path.unlink()
