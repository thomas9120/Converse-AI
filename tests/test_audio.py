from conversational_harness.audio import make_tone_wav


def test_make_tone_wav_has_wav_header():
    data = make_tone_wav(duration_s=0.01)

    assert data[:4] == b"RIFF"
    assert data[8:12] == b"WAVE"
    assert b"data" in data[:44]
