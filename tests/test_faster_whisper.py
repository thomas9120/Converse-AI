import asyncio
import struct
from types import SimpleNamespace

from conversational_harness.providers.faster_whisper import FasterWhisperASRProvider


class FakeWhisperModel:
    def __init__(self):
        self.kwargs = {}

    def transcribe(self, audio, **kwargs):
        assert audio.dtype.name == "float32"
        assert audio.size > 0
        self.kwargs = kwargs
        return [SimpleNamespace(text=" hello "), SimpleNamespace(text=" harness ")], SimpleNamespace()


def make_pcm(samples):
    return struct.pack(f"<{len(samples)}h", *samples)


def test_faster_whisper_load_is_noop_when_model_is_loaded():
    model = FakeWhisperModel()
    provider = FasterWhisperASRProvider({"_model": model})

    status = asyncio.run(provider.load())

    assert status.ready
    assert provider._model is model


def test_faster_whisper_load_reports_failure(monkeypatch):
    provider = FasterWhisperASRProvider({"model": "missing", "timeout_s": 1})

    def fail():
        raise RuntimeError("load failed")

    monkeypatch.setattr(provider, "_ensure_model", fail)

    try:
        asyncio.run(provider.load())
    except RuntimeError as exc:
        assert "load failed" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_faster_whisper_transcribe_audio_uses_loaded_model():
    model = FakeWhisperModel()
    provider = FasterWhisperASRProvider({"_model": model, "language": "en"})

    async def run():
        events = []
        async for event in provider.transcribe_audio(make_pcm([0, 1200, -1200, 0]), 16000):
            events.append(event)
        return events

    events = asyncio.run(run())

    assert len(events) == 1
    assert events[0].final
    assert events[0].text == "hello harness"
    assert model.kwargs["condition_on_previous_text"] is False
    assert model.kwargs["temperature"] == 0
    assert model.kwargs["compression_ratio_threshold"] == 2.4
    assert model.kwargs["log_prob_threshold"] == -0.5
    assert model.kwargs["no_speech_threshold"] == 0.2


def test_faster_whisper_allows_profile_overrides():
    model = FakeWhisperModel()
    provider = FasterWhisperASRProvider(
        {
            "_model": model,
            "condition_on_previous_text": True,
            "temperature": 0.2,
            "compression_ratio_threshold": None,
            "log_prob_threshold": -1.0,
            "no_speech_threshold": 0.6,
            "suppress_tokens": "-1,50364",
        }
    )

    async def run():
        events = []
        async for event in provider.transcribe_audio(make_pcm([0, 1200, -1200, 0]), 16000):
            events.append(event)
        return events

    asyncio.run(run())

    assert model.kwargs["condition_on_previous_text"] is True
    assert model.kwargs["temperature"] == 0.2
    assert model.kwargs["compression_ratio_threshold"] is None
    assert model.kwargs["log_prob_threshold"] == -1.0
    assert model.kwargs["no_speech_threshold"] == 0.6
    assert model.kwargs["suppress_tokens"] == "-1,50364"


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
