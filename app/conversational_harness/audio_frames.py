from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np


SUPPORTED_ENCODING = "pcm_s16le"


@dataclass(frozen=True)
class AudioFrame:
    data: bytes
    sequence: int
    sample_rate: int
    channels: int
    frame_ms: int
    encoding: str


@dataclass
class AudioFrameStats:
    expected_sample_rate: int
    expected_channels: int
    expected_frame_ms: int
    received_frames: int = 0
    dropped_frames: int = 0
    last_sequence: int | None = None
    last_emit_ts: float = field(default_factory=time.perf_counter)

    def update(self, frame: AudioFrame) -> dict[str, Any] | None:
        if self.last_sequence is not None and frame.sequence > self.last_sequence + 1:
            self.dropped_frames += frame.sequence - self.last_sequence - 1
        self.last_sequence = frame.sequence
        self.received_frames += 1

        now = time.perf_counter()
        if now - self.last_emit_ts < 0.1:
            return None
        self.last_emit_ts = now
        level = compute_pcm16_level(frame.data)
        return {
            "sequence": frame.sequence,
            "received_frames": self.received_frames,
            "dropped_frames": self.dropped_frames,
            "rms": level["rms"],
            "peak": level["peak"],
            "sample_rate": frame.sample_rate,
            "channels": frame.channels,
            "frame_ms": frame.frame_ms,
        }


def parse_audio_frame(payload: dict[str, Any], expected: AudioFrameStats) -> AudioFrame:
    sample_rate = int(payload.get("sample_rate", 0))
    channels = int(payload.get("channels", 0))
    frame_ms = int(payload.get("frame_ms", 0))
    sequence = int(payload.get("sequence", -1))
    encoding = str(payload.get("encoding", ""))
    encoded_data = payload.get("data")

    if sample_rate != expected.expected_sample_rate:
        raise ValueError(
            f"expected sample_rate {expected.expected_sample_rate}, got {sample_rate}"
        )
    if channels != expected.expected_channels:
        raise ValueError(
            f"expected channels {expected.expected_channels}, got {channels}"
        )
    if frame_ms != expected.expected_frame_ms:
        raise ValueError(
            f"expected frame_ms {expected.expected_frame_ms}, got {frame_ms}"
        )
    if encoding != SUPPORTED_ENCODING:
        raise ValueError(f"expected encoding {SUPPORTED_ENCODING}, got {encoding}")
    if sequence < 0:
        raise ValueError("sequence must be a non-negative integer")
    if not isinstance(encoded_data, str) or not encoded_data:
        raise ValueError("data must be a non-empty base64 string")

    try:
        data = base64.b64decode(encoded_data, validate=True)
    except Exception as exc:
        raise ValueError("data must be valid base64") from exc

    expected_samples = sample_rate * frame_ms // 1000
    expected_bytes = expected_samples * channels * 2
    if len(data) != expected_bytes:
        raise ValueError(f"expected {expected_bytes} audio bytes, got {len(data)}")

    return AudioFrame(
        data=data,
        sequence=sequence,
        sample_rate=sample_rate,
        channels=channels,
        frame_ms=frame_ms,
        encoding=encoding,
    )


def compute_pcm16_level(data: bytes) -> dict[str, float]:
    if not data:
        return {"rms": 0.0, "peak": 0.0}
    arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
    if len(arr) == 0:
        return {"rms": 0.0, "peak": 0.0}
    peak = float(np.abs(arr).max())
    rms = float(np.sqrt(np.mean(arr**2)))
    return {"rms": round(rms, 4), "peak": round(peak, 4)}


def trim_pcm16_silence(data: bytes, *, frame_ms: int, sample_rate: int, rms_threshold: float) -> bytes:
    if not data or rms_threshold <= 0:
        return data
    bytes_per_frame = max(1, sample_rate * frame_ms // 1000 * 2)
    frames = [
        data[index : index + bytes_per_frame]
        for index in range(0, len(data), bytes_per_frame)
        if len(data[index : index + bytes_per_frame]) == bytes_per_frame
    ]
    if not frames:
        return data

    start = 0
    while start < len(frames) and compute_pcm16_level(frames[start])["rms"] < rms_threshold:
        start += 1

    end = len(frames) - 1
    while end >= start and compute_pcm16_level(frames[end])["rms"] < rms_threshold:
        end -= 1

    if start > end:
        return b""
    return b"".join(frames[start : end + 1])
