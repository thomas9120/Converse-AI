import asyncio
import struct
from types import SimpleNamespace

from conversational_harness.providers.faster_whisper import FasterWhisperASRProvider


class FakeWhisperModel:
    def transcribe(self, audio, **kwargs):
        assert audio.dtype.name == "float32"
        assert audio.size > 0
        return [SimpleNamespace(text=" hello "), SimpleNamespace(text=" harness ")], SimpleNamespace()


def make_pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def test_faster_whisper_transcribe_audio_uses_loaded_model():
    provider = FasterWhisperASRProvider({"_model": FakeWhisperModel(), "language": "en"})

    async def run():
        events = []
        async for event in provider.transcribe_audio(make_pcm([0, 1200, -1200, 0]), 16000):
            events.append(event)
        return events

    events = asyncio.run(run())

    assert len(events) == 1
    assert events[0].final
    assert events[0].text == "hello harness"


def test_faster_whisper_rejects_wrong_sample_rate():
    provider = FasterWhisperASRProvider({"_model": FakeWhisperModel()})

    async def run():
        events = []
        async for event in provider.transcribe_audio(make_pcm([0, 1]), 48000):
            events.append(event)

    try:
        asyncio.run(run())
    except ValueError as exc:
        assert "16000" in str(exc)
    else:
        raise AssertionError("expected ValueError")
