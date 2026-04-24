import struct

from conversational_harness.audio_frames import AudioFrame
from conversational_harness.providers.factory import build_vad
from conversational_harness.providers.silero import SileroVADProvider


class FakeTensor:
    def __init__(self, value):
        self.value = value

    def item(self):
        return self.value


class FakeModel:
    def __init__(self, probabilities):
        self.probabilities = list(probabilities)
        self.reset_count = 0

    def __call__(self, chunk, sample_rate):
        return FakeTensor(self.probabilities.pop(0) if self.probabilities else 0.0)

    def reset_states(self):
        self.reset_count += 1


class FakeTorch:
    float32 = "float32"

    def tensor(self, samples, dtype=None):
        return FakeTorchTensor(samples)


class FakeTorchTensor:
    def __init__(self, samples):
        self.samples = samples

    def __truediv__(self, divisor):
        return self


def make_frame(sequence=0, sample_rate=16000, frame_ms=32):
    samples = [1000] * (sample_rate * frame_ms // 1000)
    return AudioFrame(
        data=struct.pack(f"<{len(samples)}h", *samples),
        sequence=sequence,
        sample_rate=sample_rate,
        channels=1,
        frame_ms=frame_ms,
        encoding="pcm_s16le",
    )


def test_build_vad_returns_silero_provider():
    provider = build_vad({"provider": "silero", "_model": FakeModel([0.1])})

    assert isinstance(provider, SileroVADProvider)


def test_silero_vad_emits_speech_start_and_end():
    provider = SileroVADProvider(
        {
            "_model": FakeModel([0.8, 0.2, 0.2]),
            "speech_threshold": 0.5,
            "neg_threshold": 0.35,
            "hangover_ms": 64,
            "window_samples": 512,
            "sample_rate": 16000,
        }
    )
    provider._torch = FakeTorch()

    import asyncio

    async def run_frames():
        events = []
        for sequence in range(3):
            events.extend(await provider.process_frame(make_frame(sequence=sequence)))
        return [event.type for event in events]

    event_types = asyncio.run(run_frames())

    assert "vad.speech_start" in event_types
    assert "vad.speech_end" in event_types
    assert event_types.count("vad.probability") == 3
