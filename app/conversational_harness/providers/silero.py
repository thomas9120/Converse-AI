from __future__ import annotations

import struct
from typing import Protocol

from conversational_harness.audio_frames import AudioFrame
from conversational_harness.providers.base import ProviderCapabilities, ProviderStatus, VADEvent, VADProvider


class SileroModel(Protocol):
    def __call__(self, chunk, sample_rate: int):
        ...

    def reset_states(self) -> None:
        ...


class SileroVADProvider(VADProvider):
    def __init__(self, config: dict):
        self.threshold = float(config.get("speech_threshold", 0.5))
        self.neg_threshold = float(config.get("neg_threshold", max(0.15, self.threshold - 0.15)))
        self.hangover_ms = int(config.get("hangover_ms", 450))
        self.window_samples = int(config.get("window_samples", 512))
        self.sample_rate = int(config.get("sample_rate", 16000))
        self._model: SileroModel | None = config.get("_model")
        self._torch = None
        self._buffer = bytearray()
        self._speaking = False
        self._silence_ms = 0
        self._audio_ms = 0
        self._load_error: str | None = None

    @property
    def status(self) -> ProviderStatus:
        ready = self._model is not None and self._load_error is None
        message = "Silero VAD ONNX model loaded." if ready else "Silero VAD is configured and will load on first status check."
        if self._load_error:
            message = f"Silero VAD failed to load: {self._load_error}"
        return ProviderStatus(
            name="silero-vad",
            kind="vad",
            ready=ready,
            message=message,
            capabilities=ProviderCapabilities(supports_barge_in=True),
        )

    async def check_status(self) -> ProviderStatus:
        self._ensure_model()
        return self.status

    async def process_frame(self, frame: AudioFrame) -> list[VADEvent]:
        self._ensure_model()
        if self._model is None or self._torch is None:
            return []
        if frame.sample_rate != self.sample_rate:
            raise ValueError(f"Silero VAD expected {self.sample_rate} Hz audio, got {frame.sample_rate}")

        self._buffer.extend(frame.data)
        events: list[VADEvent] = []
        window_bytes = self.window_samples * 2
        while len(self._buffer) >= window_bytes:
            chunk_bytes = bytes(self._buffer[:window_bytes])
            del self._buffer[:window_bytes]
            probability = self._infer_probability(chunk_bytes)
            self._audio_ms += int(self.window_samples * 1000 / self.sample_rate)
            transition = self._update_state(probability)
            events.append(VADEvent("vad.probability", probability, self._audio_ms))
            if transition:
                events.append(VADEvent(transition, probability, self._audio_ms))
        return events

    def reset(self) -> None:
        self._buffer.clear()
        self._speaking = False
        self._silence_ms = 0
        self._audio_ms = 0
        if self._model:
            self._model.reset_states()

    def _ensure_model(self) -> None:
        if self._model is not None or self._load_error:
            return
        try:
            from silero_vad import load_silero_vad
            import torch

            self._torch = torch
            self._model = load_silero_vad(onnx=True)
        except Exception as exc:
            self._load_error = str(exc)

    def _infer_probability(self, chunk_bytes: bytes) -> float:
        samples = struct.unpack(f"<{self.window_samples}h", chunk_bytes)
        tensor = self._torch.tensor(samples, dtype=self._torch.float32) / 32768.0
        result = self._model(tensor, self.sample_rate)
        return round(float(result.item()), 4)

    def _update_state(self, probability: float) -> str | None:
        if probability >= self.threshold:
            self._silence_ms = 0
            if not self._speaking:
                self._speaking = True
                return "vad.speech_start"
            return None

        if self._speaking and probability < self.neg_threshold:
            self._silence_ms += int(self.window_samples * 1000 / self.sample_rate)
            if self._silence_ms >= self.hangover_ms:
                self._speaking = False
                self._silence_ms = 0
                return "vad.speech_end"
        return None
