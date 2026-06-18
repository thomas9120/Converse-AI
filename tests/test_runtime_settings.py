import base64
import json
import struct

import pytest

from conversational_harness.runtime_settings import (
    CharacterCard,
    RuntimeSettings,
    parse_character_png,
)


def _make_text_chunk(keyword: str, value: str) -> bytes:
    chunk_data = keyword.encode("latin-1") + b"\x00" + value.encode("latin-1")
    return (
        struct.pack(">I", len(chunk_data))
        + b"tEXt"
        + chunk_data
        + b"\x00\x00\x00\x00"
    )


def _make_png(chara_b64: str | None, *, include_iend: bool = True) -> bytes:
    png = b"\x89PNG\r\n\x1a\n"
    if chara_b64 is not None:
        png += _make_text_chunk("chara", chara_b64)
    if include_iend:
        png += struct.pack(">I", 0) + b"IEND" + b"\x00\x00\x00\x00"
    return png


def _encode_chara(card: dict) -> str:
    return base64.b64encode(json.dumps(card).encode("utf-8")).decode("ascii")


def test_parse_character_png_round_trip():
    card = {
        "name": "Lyra",
        "description": "Steady companion.",
        "personality": "Calm",
        "first_mes": "Hello {{user}}, I am {{char}}.",
        "mes_example": "<START>\n{{user}}: hi\n{{char}}: hello",
    }
    png = _make_png(_encode_chara(card))

    parsed = parse_character_png(png)

    assert isinstance(parsed, CharacterCard)
    assert parsed.name == "Lyra"
    assert parsed.description == "Steady companion."
    assert parsed.personality == "Calm"
    assert parsed.first_mes == "Hello {{user}}, I am {{char}}."
    assert parsed.mes_example == "<START>\n{{user}}: hi\n{{char}}: hello"


def test_parse_character_png_accepts_tavernai_v2_data_wrapper():
    card = {"spec": "chara_card_v2", "data": {"name": "Wrapped", "description": "V2"}}
    png = _make_png(_encode_chara(card))

    parsed = parse_character_png(png)

    assert parsed.name == "Wrapped"
    assert parsed.description == "V2"


def test_parse_character_png_rejects_non_png():
    with pytest.raises(ValueError, match="Not a valid PNG"):
        parse_character_png(b"not a png file at all")


def test_parse_character_png_rejects_png_without_chara_chunk():
    png = _make_png(None, include_iend=True)

    with pytest.raises(ValueError, match="No character card data"):
        parse_character_png(png)


def test_parse_character_png_uses_first_chara_chunk():
    first = _encode_chara({"name": "First"})
    second = _encode_chara({"name": "Second"})
    png = b"\x89PNG\r\n\x1a\n"
    png += _make_text_chunk("chara", first)
    png += _make_text_chunk("chara", second)
    png += struct.pack(">I", 0) + b"IEND" + b"\x00\x00\x00\x00"

    parsed = parse_character_png(png)

    assert parsed.name == "First"


def test_set_server_defaults_maps_n_predict_to_max_tokens():
    settings = RuntimeSettings()

    settings.set_server_defaults(
        {"n_predict": 512, "temperature": 0.8, "rogue_key": 1}
    )

    assert settings.server_defaults["max_tokens"] == 512
    assert settings.server_defaults["temperature"] == 0.8
    assert "rogue_key" not in settings.server_defaults
    assert "n_predict" not in settings.server_defaults


def test_set_server_defaults_drops_max_tokens_when_unlimited():
    settings = RuntimeSettings()

    settings.set_server_defaults({"n_predict": -1, "temperature": 0.8})

    assert "max_tokens" not in settings.server_defaults
    assert settings.server_defaults["temperature"] == 0.8


def test_effective_sampler_merges_server_profile_and_overrides():
    settings = RuntimeSettings(profile_defaults={"temperature": 0.9, "top_p": 0.95})
    settings.set_server_defaults({"temperature": 0.8, "top_k": 40, "n_predict": -1})
    settings.llm_overrides = {"temperature": 0.5}

    merged = settings.effective_sampler()

    assert merged["temperature"] == 0.5
    assert merged["top_p"] == 0.95
    assert merged["top_k"] == 40
    assert "max_tokens" not in merged


def test_effective_sampler_skips_none_values():
    settings = RuntimeSettings(
        profile_defaults={"temperature": None, "top_p": 0.95}
    )
    settings.set_server_defaults({"temperature": 0.8})
    settings.llm_overrides = {"top_p": None}

    merged = settings.effective_sampler()

    assert merged["temperature"] == 0.8
    assert merged["top_p"] == 0.95


def test_sampler_display_resolves_per_key_precedence():
    settings = RuntimeSettings(profile_defaults={"temperature": 0.9, "top_p": 0.95})
    settings.set_server_defaults({"temperature": 0.8, "top_k": 40, "n_predict": -1})
    settings.llm_overrides = {"temperature": 0.5}

    display = settings.sampler_display()

    assert display["temperature"] == 0.5
    assert display["top_p"] == 0.95
    assert display["top_k"] == 40
    assert "max_tokens" not in display


def test_effective_sampler_delegates_to_companion_mode():
    settings = RuntimeSettings(profile_defaults={"temperature": 0.9})
    settings.set_server_defaults({"temperature": 0.8, "top_k": 40})
    settings.companion.llm_overrides = {"temperature": 0.3}
    settings.active_mode = "companion"

    merged = settings.effective_sampler()
    companion_merged = settings.companion.effective_sampler(
        settings.profile_defaults, settings.server_defaults
    )

    assert merged == companion_merged
    assert merged["temperature"] == 0.3


def test_apply_patch_filters_non_sampler_keys_from_llm_overrides():
    settings = RuntimeSettings()

    settings.apply_patch({"llm_overrides": {"temperature": 0.5, "evil_key": "inject"}})

    assert settings.llm_overrides == {"temperature": 0.5}
