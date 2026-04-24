from __future__ import annotations

import math
import struct
import wave
from io import BytesIO

import numpy as np


def make_tone_wav(duration_s: float = 0.18, frequency: float = 440.0, sample_rate: int = 16000) -> bytes:
    """Create a tiny mono PCM WAV tone for mock TTS smoke paths."""
    samples = max(1, int(duration_s * sample_rate))
    pcm = bytearray()
    amplitude = 0.18
    for index in range(samples):
        value = int(32767 * amplitude * math.sin(2 * math.pi * frequency * index / sample_rate))
        pcm.extend(struct.pack("<h", value))

    data_size = len(pcm)
    byte_rate = sample_rate * 2
    header = b"".join(
        [
            b"RIFF",
            struct.pack("<I", 36 + data_size),
            b"WAVEfmt ",
            struct.pack("<IHHIIHH", 16, 1, 1, sample_rate, byte_rate, 2, 16),
            b"data",
            struct.pack("<I", data_size),
        ]
    )
    return header + bytes(pcm)


def pcm_s16le_to_float32(pcm_s16le: bytes) -> np.ndarray:
    if not pcm_s16le:
        return np.array([], dtype=np.float32)
    audio = np.frombuffer(pcm_s16le, dtype="<i2")
    return audio.astype(np.float32) / 32768.0


def float_audio_to_wav_bytes(audio, sample_rate: int) -> bytes:
    array = tensor_or_array_to_numpy(audio)
    if array.size == 0:
        return b""
    array = np.asarray(array, dtype=np.float32).reshape(-1)
    clipped = np.clip(array, -1.0, 1.0)
    pcm = np.where(clipped < 0, clipped * 32768, clipped * 32767).astype("<i2")
    buffer = BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())
    return buffer.getvalue()


def tensor_or_array_to_numpy(audio) -> np.ndarray:
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    return np.asarray(audio)
