from conversational_harness.config import load_config
from conversational_harness import main
from conversational_harness.main import profile_summary
from conversational_harness.providers.base import ProviderCapabilities, ProviderStatus
from conversational_harness.providers import build_providers
from conversational_harness.tts_runtime import TTSRuntimeManager, load_tts_presets


def test_mock_profile_builds_ready_providers():
    config = load_config("profiles/mock-local.json")
    providers = build_providers(config)
    statuses = providers.statuses()

    assert config.name == "mock-local"
    assert {item["kind"] for item in statuses} == {"vad", "asr", "llm", "tts"}
    assert all(item["ready"] for item in statuses)


def test_mock_profile_async_statuses_are_ready():
    import asyncio

    async def run_check():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        return await providers.check_statuses()

    statuses = asyncio.run(run_check())

    assert all(item["ready"] for item in statuses)


def test_kokoro_profile_builds_provider():
    config = load_config("profiles/llamacpp-kokoro-onnx.json")
    providers = build_providers(config)
    tts_status = next(item for item in providers.statuses() if item["kind"] == "tts")

    assert tts_status["name"] == "kokoro-onnx"


def test_profile_summary_includes_runtime_details():
    config = load_config("profiles/llamacpp-local.json")
    summary = profile_summary(config.raw)

    llm = next(item for item in summary if item["kind"] == "llm")
    asr = next(item for item in summary if item["kind"] == "asr")
    tts = next(item for item in summary if item["kind"] == "tts")

    assert llm["endpoint"] == "http://127.0.0.1:8080"
    assert asr["model"] == "large-v3-turbo"
    assert tts["voice"] == "azelma"


def test_tts_presets_load_and_match_default_profile():
    config = load_config("profiles/llamacpp-local.json")
    runtime = TTSRuntimeManager(config, load_tts_presets())

    assert runtime.selected_preset.id == "pocket-tts"
    assert len(runtime.presets) >= 2


def test_provider_statuses_for_bundle_runs_active_provider_checks(monkeypatch):
    import asyncio
    from types import SimpleNamespace

    class CheckedProvider:
        def __init__(self, kind):
            self.kind = kind

        @property
        def status(self):
            return ProviderStatus(
                name=f"{self.kind}-raw",
                kind=self.kind,
                ready=False,
                message="raw status",
                capabilities=ProviderCapabilities(),
            )

        async def check_status(self):
            return ProviderStatus(
                name=f"{self.kind}-checked",
                kind=self.kind,
                ready=True,
                message="checked status",
                capabilities=ProviderCapabilities(),
            )

    async def fake_describe():
        return {
            "status": {
                "name": "tts-runtime",
                "kind": "tts",
                "ready": True,
                "message": "runtime",
                "capabilities": {},
            }
        }

    monkeypatch.setattr(main.TTS_MANAGER, "describe", fake_describe)
    providers = SimpleNamespace(
        vad=CheckedProvider("vad"),
        asr=CheckedProvider("asr"),
        llm=CheckedProvider("llm"),
    )

    statuses = asyncio.run(main.provider_statuses_for_bundle(providers))

    assert [status["name"] for status in statuses[:3]] == [
        "vad-checked",
        "asr-checked",
        "llm-checked",
    ]
    assert all(status["ready"] for status in statuses)
