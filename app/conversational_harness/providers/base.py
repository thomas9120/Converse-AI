from __future__ import annotations

from dataclasses import dataclass
from typing import AsyncIterator, Awaitable, Callable, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    supports_partials: bool = False
    supports_streaming_tts: bool = False
    supports_barge_in: bool = False
    requires_gpu: bool = False
    languages: tuple[str, ...] = ("en",)


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    kind: str
    ready: bool
    message: str
    capabilities: ProviderCapabilities


@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    final: bool


@dataclass(frozen=True)
class AudioChunk:
    data: bytes
    mime_type: str
    final: bool = False


@dataclass(frozen=True)
class VADEvent:
    type: str
    probability: float
    audio_ms: int


ProgressCallback = Callable[[str, dict], Awaitable[None]]


class VADProvider(Protocol):
    @property
    def status(self) -> ProviderStatus:
        ...

    async def check_status(self) -> ProviderStatus:
        ...

    async def process_frame(self, frame) -> list[VADEvent]:
        ...


class ASRProvider(Protocol):
    @property
    def status(self) -> ProviderStatus:
        ...

    async def check_status(self) -> ProviderStatus:
        ...

    async def transcribe_text_input(self, text: str) -> AsyncIterator[TranscriptEvent]:
        ...

    async def transcribe_audio(
        self, pcm_s16le: bytes, sample_rate: int, progress: ProgressCallback | None = None
    ) -> AsyncIterator[TranscriptEvent]:
        ...


class LLMProvider(Protocol):
    @property
    def status(self) -> ProviderStatus:
        ...

    async def check_status(self) -> ProviderStatus:
        ...

    async def stream_response(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        ...


class TTSProvider(Protocol):
    @property
    def status(self) -> ProviderStatus:
        ...

    async def check_status(self) -> ProviderStatus:
        ...

    async def stream_audio(self, text: str) -> AsyncIterator[AudioChunk]:
        ...

    async def stream_audio_with_progress(
        self, text: str, progress: ProgressCallback | None = None
    ) -> AsyncIterator[AudioChunk]:
        ...
