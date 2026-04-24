import asyncio

from conversational_harness.config import load_config
from conversational_harness.tts_runtime import TTSRuntimeManager, load_tts_presets


def test_tts_runtime_switches_only_tts_provider():
    async def run():
        config = load_config("profiles/llamacpp-local.json")
        runtime = TTSRuntimeManager(config, load_tts_presets())
        before = runtime.current_turn_config()
        result = await runtime.select_preset("kokoro-v1")
        after = runtime.current_turn_config()
        return result, before, after

    result, before, after = asyncio.run(run())

    assert result["selected_preset_id"] == "kokoro-v1"
    assert result["status"]["kind"] == "tts"
    assert before["barge_in"] == after["barge_in"]


def test_tts_runtime_unload_marks_provider_unloaded():
    async def run():
        config = load_config("profiles/llamacpp-local.json")
        runtime = TTSRuntimeManager(config, load_tts_presets())
        await runtime.load_selected()
        return await runtime.unload_selected()

    result = asyncio.run(run())

    assert result["status"]["selected"]
    assert result["status"]["provider_id"] == "pocket-tts"
    assert not result["status"]["loaded"]


def test_kokoro_preset_reports_managed_controls():
    async def run():
        config = load_config("profiles/llamacpp-local.json")
        runtime = TTSRuntimeManager(config, load_tts_presets())
        return await runtime.select_preset("kokoro-v1")

    result = asyncio.run(run())

    assert not result["status"]["managed_externally"]
    assert result["status"]["supports_model_management"]


def test_tts_runtime_switches_voice_within_selected_preset():
    async def run():
        config = load_config("profiles/llamacpp-local.json")
        runtime = TTSRuntimeManager(config, load_tts_presets())
        await runtime.select_preset("kokoro-v1")
        result = await runtime.select_voice("bf_emma")
        return result, runtime.merged_profile_raw()

    result, merged = asyncio.run(run())

    assert result["selected_voice"] == "bf_emma"
    assert merged["tts"]["voice"] == "bf_emma"
