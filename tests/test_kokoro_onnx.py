import asyncio
import numpy as np

from conversational_harness.providers.factory import build_tts
from conversational_harness.providers.kokoro_onnx import KokoroOnnxProvider


class FakeKokoroModel:
    def __init__(self):
        self.calls = []

    def create(self, text, voice, speed=1.0, lang="en-us", trim=True):
        self.calls.append(
            {
                "text": text,
                "voice": voice,
                "speed": speed,
                "lang": lang,
                "trim": trim,
            }
        )
        return np.zeros(2400, dtype=np.float32), 24000


def test_build_tts_returns_kokoro_provider():
    provider = build_tts({"provider": "kokoro-onnx", "voice": "af_heart", "_model": FakeKokoroModel()})

    assert isinstance(provider, KokoroOnnxProvider)


def test_kokoro_onnx_streams_pcm_with_progress():
    provider = KokoroOnnxProvider(
        {
            "_model": FakeKokoroModel(),
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
        return chunks, progress_events, provider

    chunks, progress_events, provider = asyncio.run(run())

    assert chunks
    assert chunks[0].encoding == "pcm_s16le"
    assert chunks[0].sample_rate == 24000
    assert chunks[0].channels == 1
    assert chunks[0].final is True
    assert ("tts.progress", "complete") in progress_events
    assert provider._model.calls[0]["voice"] == "af_heart"
