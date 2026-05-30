import asyncio
import sys
import types

import numpy as np

from conversational_harness.providers.factory import build_tts
from conversational_harness.providers.pocket_tts import PocketTTSProvider


class FakePocketModel:
    sample_rate = 24000

    def __init__(self):
        self.voice_requests = []

    def get_state_for_audio_prompt(self, voice):
        self.voice_requests.append(voice)
        return {"voice": voice}

    def generate_audio_stream(self, voice_state, text, max_tokens=50, copy_state=True):
        assert voice_state["voice"] == "azelma"
        assert text
        yield np.zeros(240, dtype=np.float32)


def test_build_tts_returns_pocket_provider():
    provider = build_tts({"provider": "pocket-tts", "voice": "azelma", "_model": FakePocketModel()})

    assert isinstance(provider, PocketTTSProvider)


def test_pocket_tts_streams_pcm_with_progress():
    provider = PocketTTSProvider({"_model": FakePocketModel(), "voice": "azelma", "coalesce_ms": 5})

    async def run():
        progress_events = []

        async def progress(event_type, payload):
            progress_events.append((event_type, payload["stage"]))

        chunks = []
        async for chunk in provider.stream_audio_with_progress("Hello there.", progress):
            chunks.append(chunk)
        return chunks, progress_events

    chunks, progress_events = asyncio.run(run())

    assert chunks
    assert chunks[0].encoding == "pcm_s16le"
    assert chunks[0].sample_rate == 24000
    assert chunks[0].channels == 1
    assert chunks[0].mime_type is None
    assert ("tts.progress", "complete") in progress_events


def test_pocket_tts_forwards_quantize_to_loader(monkeypatch):
    load_kwargs = {}

    class FakeTTSModel:
        @classmethod
        def load_model(cls, **kwargs):
            load_kwargs.update(kwargs)
            return FakePocketModel()

    monkeypatch.setitem(sys.modules, "pocket_tts", types.SimpleNamespace(TTSModel=FakeTTSModel))
    provider = PocketTTSProvider({"voice": "azelma", "quantize": True, "language": "english"})

    asyncio.run(provider.load())

    assert load_kwargs["quantize"] is True
    assert load_kwargs["language"] == "english"
    assert provider.status.loaded is True
    assert "int8" in provider.status.message
