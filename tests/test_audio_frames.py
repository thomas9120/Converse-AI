import base64
import struct

import pytest

from conversational_harness.audio_frames import (
    AudioFrameStats,
    compute_pcm16_level,
    parse_audio_frame,
    trim_pcm16_silence,
)


def make_payload(sequence=0, sample_rate=16000, channels=1, frame_ms=30, samples=None):
    if samples is None:
        samples = [0] * (sample_rate * frame_ms // 1000)
    data = struct.pack(f"<{len(samples)}h", *samples)
    return {
        "encoding": "pcm_s16le",
        "sample_rate": sample_rate,
        "channels": channels,
        "frame_ms": frame_ms,
        "sequence": sequence,
        "data": base64.b64encode(data).decode("ascii"),
    }


def test_parse_audio_frame_accepts_expected_pcm():
    stats = AudioFrameStats(expected_sample_rate=16000, expected_channels=1, expected_frame_ms=30)

    frame = parse_audio_frame(make_payload(sequence=3), stats)

    assert frame.sequence == 3
    assert len(frame.data) == 960


def test_parse_audio_frame_rejects_wrong_sample_rate():
    stats = AudioFrameStats(expected_sample_rate=16000, expected_channels=1, expected_frame_ms=30)

    with pytest.raises(ValueError, match="sample_rate"):
        parse_audio_frame(make_payload(sample_rate=48000), stats)


def test_compute_pcm16_level_reports_peak_and_rms():
    data = struct.pack("<4h", 0, 32767, -32768, 0)

    level = compute_pcm16_level(data)

    assert level["peak"] == 1.0
    assert 0.70 < level["rms"] < 0.72


def test_audio_frame_stats_tracks_dropped_frames():
    stats = AudioFrameStats(expected_sample_rate=16000, expected_channels=1, expected_frame_ms=30)

    first = parse_audio_frame(make_payload(sequence=0), stats)
    second = parse_audio_frame(make_payload(sequence=3), stats)
    stats.update(first)
    stats.last_emit_ts = 0
    metrics = stats.update(second)

    assert metrics is not None
    assert metrics["dropped_frames"] == 2


def test_trim_pcm16_silence_removes_quiet_edges():
    quiet = [0] * 480
    speech = [1200] * 480
    data = struct.pack(f"<{len(quiet + speech + quiet)}h", *(quiet + speech + quiet))

    trimmed = trim_pcm16_silence(data, frame_ms=30, sample_rate=16000, rms_threshold=0.003)

    assert trimmed == struct.pack(f"<{len(speech)}h", *speech)


def test_trim_pcm16_silence_returns_empty_for_all_quiet_audio():
    data = struct.pack("<480h", *([0] * 480))

    trimmed = trim_pcm16_silence(data, frame_ms=30, sample_rate=16000, rms_threshold=0.003)

    assert trimmed == b""
