import asyncio

from conversational_harness.config import load_config
from conversational_harness.tts_runtime import TTSRuntimeManager, load_tts_presets


def test_tts_runtime_switches_only_tts_provider():
    async def run():
        config = load_config("profiles/llamacpp-local.json")
        runtime = TTSRuntimeManager(config, load_tts_presets())
        before = runtime.current_turn_config()
        result = await runtime.select_preset("kyutai-0.75b")
        after = runtime.current_turn_config()
        return result, before, after

    result, before, after = asyncio.run(run())

    assert result["selected_preset_id"] == "kyutai-0.75b"
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


def test_external_tts_preset_reports_unmanaged_controls():
    async def run():
        config = load_config("profiles/llamacpp-local.json")
        runtime = TTSRuntimeManager(config, load_tts_presets())
        return await runtime.select_preset("kyutai-1.6b")

    result = asyncio.run(run())

    assert result["status"]["managed_externally"]
    assert not result["status"]["supports_model_management"]
