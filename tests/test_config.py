from conversational_harness.config import load_config
from conversational_harness.providers import build_providers


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


def test_kyutai_tts_profiles_build_server_provider():
    for profile in (
        "profiles/llamacpp-kyutai-tts-0.75b.json",
        "profiles/llamacpp-kyutai-tts-1.6b.json",
    ):
        config = load_config(profile)
        providers = build_providers(config)
        tts_status = next(item for item in providers.statuses() if item["kind"] == "tts")

        assert tts_status["name"] == "kyutai-tts-server"
