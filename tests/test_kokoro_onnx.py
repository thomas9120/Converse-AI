import asyncio
import numpy as np
import pytest

from conversational_harness.providers.factory import build_tts
from conversational_harness.providers.kokoro_onnx import KokoroOnnxProvider


class FakeKokoroModel:
    def __init__(self):
        self.calls = []

    async def create_stream(self, text, voice, speed=1.0, lang="en-us", is_phonemes=False, trim=True):
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "speed": speed,
                "lang": lang,
                "is_phonemes": is_phonemes,
                "trim": trim,
            }
        )
        yield np.zeros(1200, dtype=np.float32), 24000
        yield np.zeros(1200, dtype=np.float32), 24000


class FakeG2P:
    def __init__(self):
        self.calls = []

    def __call__(self, text):
        self.calls.append(text)
        return "həlˈO", []


class BrokenG2P:
    def __call__(self, text):
        raise RuntimeError("boom")


def test_build_tts_returns_kokoro_provider():
    provider = build_tts({"provider": "kokoro-onnx", "voice": "af_heart", "_model": FakeKokoroModel()})

    assert isinstance(provider, KokoroOnnxProvider)


def test_kokoro_onnx_streams_pcm_with_progress():
    provider = KokoroOnnxProvider(
        {
            "_model": FakeKokoroModel(),
            "_g2p": FakeG2P(),
            "voice": "af_heart",
            "lang": "en-us",
            "speed": 1.0,
        }
    )

    async def run():
        progress_events = []

        async def progress(event_type, payload):
            progress_events.append((event_type, payload["stage"]))

        chunks = []
        async for chunk in provider.stream_audio_with_progress("Hello there.", progress):
            chunks.append(chunk)
        await asyncio.sleep(0)
        return chunks, progress_events, provider

    chunks, progress_events, provider = asyncio.run(run())

    assert chunks
    assert len(chunks) == 2
    assert chunks[0].encoding == "pcm_s16le"
    assert chunks[0].sample_rate == 24000
    assert chunks[0].channels == 1
    assert chunks[0].final is False
    assert chunks[1].final is True
    assert ("tts.progress", "phonemizing") in progress_events
    assert ("tts.progress", "complete") in progress_events
    assert provider._model.calls[0]["voice"] == "af_heart"
    assert provider._model.calls[0]["is_phonemes"] is True
    assert provider._model.calls[0]["text"] == "həlˈO"
    assert provider.status.capabilities.supports_streaming_tts is True


def test_kokoro_onnx_raises_clear_error_when_misaki_fails():
    provider = KokoroOnnxProvider(
        {
            "_model": FakeKokoroModel(),
            "_g2p": BrokenG2P(),
            "voice": "af_heart",
            "lang": "en-us",
            "speed": 1.0,
        }
    )

    async def run():
        chunks = []
        async for chunk in provider.stream_audio_with_progress("Hello there."):
            chunks.append(chunk)
        return chunks

    with pytest.raises(RuntimeError, match="Misaki English phonemization failed: boom"):
        asyncio.run(run())
