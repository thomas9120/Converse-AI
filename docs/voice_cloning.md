# Plan: Pocket TTS Voice Cloning

## Goal

Add user voice cloning to the existing `pocket-tts` TTS provider so users can record or upload a short sample of a voice and have the harness speak in that voice, behind the same `TTSProvider` interface and TTS runtime that built-in voices use today.

---

## Verdict

Pocket TTS 2.1.0 (already pinned in `requirements.txt`) is designed for voice cloning. The relevant API entry points live in `pocket_tts/models/tts_model.py`:

- **`TTSModel.get_state_for_audio_prompt(audio_conditioning, truncate=False)`** accepts:
  - A `Path` to a local audio file (WAV, FLAC, OGG — anything `audio_read` + `soundfile` can decode).
  - A `str` URL (`http://`, `https://`, `hf://`).
  - A pre-built voice name (e.g. `alba`).
  - A `.safetensors` file with a pre-encoded voice state (fast path — skips re-encoding).
  - A `torch.Tensor` of shape `[channels, samples]`.
- **`export_model_state(state, dest)`** writes the encoded state to a `.safetensors` for instant reuse.
- Audio is auto-resampled to **24 kHz mono** (model's `mimi.sample_rate`).
- `truncate=True` clips to 30 s. The library's own HTTP server uses this; we will offer the same.

No new Python dependencies are required. `requirements.txt` is unchanged.

---

## Caveats

1. **Model weights are gated.** The `english` config (`pocket_tts/config/english.yaml`) points to `hf://kyutai/pocket-tts/...`, which is gated. If the user has not accepted the HF terms and logged in, the loader silently falls back to `kyutai/pocket-tts-without-voice-cloning` and sets `tts_model.has_voice_cloning = False`. Calling `get_state_for_audio_prompt` then raises `ValueError(VOICE_CLONING_UNSUPPORTED)`. The UI must surface this clearly.
2. **The current provider only accepts a voice name string.** `providers/pocket_tts.py:178` does `self._model.get_state_for_audio_prompt(self.voice)`. For a local file path, this also works *if* we pass a `Path`. So no new library install is needed — just provider config plumbing.

---

## Tasks

### 1. New `VoiceStore` (disk CRUD)

**File:** `app/conversational_harness/voice_store.py` (new)

A small class that owns `voices/cloned/` on disk plus a JSON index:

```
voices/
  cloned/
    .gitkeep
    index.json              # { voices: [{id, label, source, wav_path, safetensors_path, sample_rate, duration_s, created_at}] }
```

Responsibilities:

- `add(label, file_bytes) -> dict` — writes wav, registers in `index.json`, returns the new voice record.
- `list() -> list[dict]` — reads the index, returns relative paths resolved to absolute under `PROJECT_ROOT`.
- `remove(voice_id) -> dict` — deletes wav + safetensors cache + index entry.
- All paths stored in the index are **relative to `PROJECT_ROOT`** so a moved checkout still works.
- Refuses to write outside `voices/cloned/` (defence against path traversal from crafted IDs).

### 2. New `VoiceCloneService` (encode + cache)

**File:** `app/conversational_harness/voice_clone.py` (new)

Thin wrapper around the `pocket_tts` encode/cache pipeline:

- `has_voice_cloning(model) -> bool` — probes `model.has_voice_cloning`.
- `encode_to_cache(model, wav_path, safetensors_path) -> Path` — runs `get_state_for_audio_prompt` once and writes the result via `pocket_tts.export_model_state`.
- `validate_upload(file_bytes) -> (sample_rate, duration_s)` — opens with the standard `wave` module for `.wav` (no extra dep), or `soundfile` for `.flac`. Reject if duration < 1 s or > 60 s, or sample rate < 8 kHz.

### 3. Provider changes — `app/conversational_harness/providers/pocket_tts.py`

New config keys (all optional, no breaking changes to existing profiles):

| Key | Default | Purpose |
|---|---|---|
| `voice_source` | `"builtin"` | `"builtin"` \| `"file"` \| `"safetensors"` |
| `voice_path` | — | Absolute path or path relative to `PROJECT_ROOT` |
| `voice_truncate` | `True` | Match library's own HTTP server (30 s cap) |
| `voice_cache_safetensors` | `True` | Encode once, cache for fast reload |

`_ensure_model` changes:

- If `voice_source == "safetensors"`: pass `Path(voice_path)` directly with `truncate=False`.
- If `voice_source == "file"`: pass `Path(voice_path)` with `truncate=self.voice_truncate`. If `voice_cache_safetensors` is true, check for a sibling `<id>.safetensors`; on miss, encode then call `pocket_tts.export_model_state(state, dest)` (run in the existing worker thread, no event-loop changes).
- Else unchanged.

`status` additions:

- `capabilities.supports_voice_cloning: bool` — driven by `self._model.has_voice_cloning` after first load, so the UI can grey out without a separate API call.

### 4. Runtime changes — `app/conversational_harness/tts_runtime.py`

New methods on `TTSRuntimeManager`:

- `list_cloned_voices() -> list[dict]`
- `add_cloned_voice(label, file_bytes) -> dict`
- `remove_cloned_voice(voice_id) -> dict`
- `current_tts_config()` — when a cloned voice is selected, emit `voice_source="file"` (or `"safetensors"` if a cache exists) and `voice_path=<absolute>` instead of `voice="azelma"`.

`available_voices()` returns a merged, sorted list: built-ins first, then cloned (tagged `{provenance: "cloned"}`). The existing `select_voice` whitelist check is widened to accept IDs that exist in either group.

Backwards-compat: profiles that don't set `voice_source` behave exactly as today.

### 5. New API endpoints — `app/conversational_harness/main.py`

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/tts/voice/clone` | Multipart upload (`file`, optional `label`). Validates WAV/FLAC, max 10 MB, max 60 s raw duration, writes to `voices/cloned/<id>.wav`, registers in `index.json`. Returns the new voice. |
| `GET` | `/api/tts/voice/cloned` | List cloned voices. |
| `DELETE` | `/api/tts/voice/cloned/{voice_id}` | Remove from index + delete wav + delete safetensors cache. |

Limits and validation:

- `MAX_VOICE_UPLOAD_BYTES = 10 * 1024 * 1024` (a 60 s 48 kHz 24-bit WAV is ~17 MB; 10 MB at 16 kHz mono covers ~5 min — generous for voice prompts).
- Reject if duration < 1 s or > 60 s.
- Reject sample rate < 8000.
- All errors follow the existing pattern (`HTTPException(400, "...")`).

### 6. Frontend — `app/static/app.js` + `index.html`

- New section under the existing TTS panel: **Cloned voices** with a file picker (`<input type="file" accept="audio/wav,audio/flac,audio/x-wav,audio/wave">`) and an "Add cloned voice" button.
- **No** in-browser recording in v1 (upload only).
- `renderTtsRuntime`:
  - Voice `<select>` gets `<optgroup label="Built-in">` and `<optgroup label="My cloned voices">` (browser-native, no extra deps).
  - Each cloned option shows the label + `(cloned · Xs)`.
- If `status.capabilities.supports_voice_cloning == false`:
  - The file picker is disabled, with a tooltip linking to the HF gating instructions.
  - Cloned voices already in `index.json` are still listed (so an admin can see them) but marked "Model does not support cloning" and the voice `<select>` falls back to built-ins.
- `withTtsBusy` wraps the upload so the UI doesn't look frozen during the encode step.

### 7. Tests

- `tests/test_voice_store.py` — CRUD + index round-trip + path-traversal refusal.
- `tests/test_voice_clone.py` — fake `TTSModel.get_state_for_audio_prompt` records the call; assert `Path` and `truncate` are forwarded correctly; assert `export_model_state` is called on first use and skipped on the second load when the cache exists.
- `tests/test_pocket_tts.py` — extend with one test that `voice_source="file"` resolves to a `Path`, not a string, in `_ensure_model`.

The existing `FakePocketModel` at `tests/test_pocket_tts.py:11` is reused.

---

## What This Plan Does NOT Change

- `TTSProvider` Protocol (`providers/base.py`) — no interface changes; voice cloning is a Pocket TTS capability, not a harness-wide one.
- The Kokoro preset, ASR, VAD, LLM, character-card system, or any profile JSON other than the local `pocket-tts` defaults.
- The browser-side audio pipeline (it already handles `pcm_s16le` from a cloned voice the same way as from a built-in).
- `requirements.txt` (no new packages — `pocket-tts` 2.1.0 already includes `get_state_for_audio_prompt` and `export_model_state`).

---

## Implementation Order

1. `voice_store.py` + `voice_clone.py` (pure helpers, fully testable in isolation).
2. `PocketTTSProvider` changes (`voice_source`, `voice_path`, cache, `has_voice_cloning` exposure).
3. `TTSRuntimeManager` integration (merge cloned voices into the picker, persist selection).
4. New HTTP endpoints in `main.py` (mirror the existing character-card upload pattern).
5. Frontend wiring in `app.js` + `index.html` (optgroups, disabled state, tooltips).
6. Tests under `tests/`.

---

## Manual Verification (post-implementation)

1. `pytest -q` from the project root — existing + new tests pass.
2. Start harness, open `http://127.0.0.1:7860`, pick the `pocket-tts` preset, upload a 10 s WAV, watch status → "loaded", speak, confirm the voice matches.
3. Restart harness, confirm the cloned voice is still listed and the first response is faster (cache hit).
4. With `hf auth` not logged in / no cloning terms accepted, confirm the upload UI is disabled with the tooltip.
