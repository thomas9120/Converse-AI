from __future__ import annotations

from dataclasses import dataclass

from conversational_harness.config import HarnessConfig
from conversational_harness.providers.base import ASRProvider, LLMProvider, TTSProvider, VADProvider
from conversational_harness.providers.faster_whisper import FasterWhisperASRProvider
from conversational_harness.providers.kyutai_tts_server import KyutaiTTSServerProvider
from conversational_harness.providers.llamacpp import LlamaCppProvider
from conversational_harness.providers.mock import MockASRProvider, MockLLMProvider, MockTTSProvider, MockVADProvider
from conversational_harness.providers.pocket_tts import PocketTTSProvider
from conversational_harness.providers.silero import SileroVADProvider
from conversational_harness.providers.unavailable import UnavailableProvider


@dataclass
class ProviderBundle:
    vad: VADProvider
    asr: ASRProvider
    llm: LLMProvider
    tts: TTSProvider

    def statuses(self) -> list[dict]:
        items = [self.vad.status, self.asr.status, self.llm.status, self.tts.status]
        return serialize_statuses(items)

    async def check_statuses(self) -> list[dict]:
        items = [
            await self.vad.check_status(),
            await self.asr.check_status(),
            await self.llm.check_status(),
            await self.tts.check_status(),
        ]
        return serialize_statuses(items)


def serialize_statuses(items) -> list[dict]:
    return [
        {
            "name": item.name,
            "kind": item.kind,
            "ready": item.ready,
            "message": item.message,
            "capabilities": item.capabilities.__dict__,
        }
        for item in items
    ]


def build_vad(config: dict) -> VADProvider:
    provider = config.get("provider", "mock")
    if provider == "mock":
        return MockVADProvider(config)
    if provider == "silero":
        return SileroVADProvider(config)
    return UnavailableProvider("vad", str(provider), f"Unknown VAD provider: {provider}")


def build_asr(config: dict) -> ASRProvider:
    provider = config.get("provider", "mock")
    if provider == "mock":
        return MockASRProvider(config)
    if provider == "kyutai":
        return UnavailableProvider(
            "asr",
            "kyutai-stt",
            "Kyutai STT requires a configured PyTorch, MLX, or moshi-server streaming adapter.",
            requires_gpu=True,
        )
    if provider == "faster-whisper":
        return FasterWhisperASRProvider(config)
    return UnavailableProvider("asr", str(provider), f"Unknown ASR provider: {provider}")


def build_llm(config: dict) -> LLMProvider:
    provider = config.get("provider", "mock")
    if provider == "mock":
        return MockLLMProvider(config)
    if provider == "llamacpp":
        return LlamaCppProvider(config)
    return UnavailableProvider("llm", str(provider), f"Unknown LLM provider: {provider}")


def build_tts(config: dict) -> TTSProvider:
    provider = config.get("provider", "mock")
    if provider == "mock":
        return MockTTSProvider(config)
    if provider in {"kyutai", "kyutai-tts-server"}:
        return KyutaiTTSServerProvider(config)
    if provider == "pocket-tts":
        return PocketTTSProvider(config)
    return UnavailableProvider("tts", str(provider), f"Unknown TTS provider: {provider}")


def build_providers(config: HarnessConfig) -> ProviderBundle:
    vad_config = config.section("vad")
    audio_config = config.section("audio")
    vad_config.setdefault("sample_rate", int(audio_config.get("sample_rate", 16000)))
    return ProviderBundle(
        vad=build_vad(vad_config),
        asr=build_asr(config.section("asr")),
        llm=build_llm(config.section("llm")),
        tts=build_tts(config.section("tts")),
    )
