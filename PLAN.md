# Plan: Rebuild Converse-AI Backend on `converse-framework`

**Status:** Brainstorming / Planning  
**Date:** 2026-07-08  

## Goal

Replace the hand-rolled speech stack in the current Converse-AI monolith with the
`converse-framework` v0.2.3 package (available on PyPI), and add **audio.cpp**
support for both ASR and TTS — a new provider backend not present in the current
codebase. The framework was extracted from an earlier version of this very
codebase and already contains equivalents of virtually all the bespoke components
in the current repo.

## Scope

### In Scope

1. Install `converse-framework` as a dependency and redirect imports.
2. Replace the inline VAD utterance-collection state machine in `main.py` with
   the framework's `AudioUtteranceCollector`.
3. Replace the custom `ConversationOrchestrator` with `SpeechPipeline`.
4. Rewire `main.py` to use `WebSocketSession` (framework's reusable
   message-dispatch loop) or a harness-side equivalent that delegates to
   framework components.
5. Add `audio-cpp` as an ASR provider option (the framework already ships
   `AudioCppASRProvider` — we just need profiles and wiring).
6. Add `audio-cpp` as a TTS provider option (`AudioCppTTSProvider`).
7. Add `whisper-cpp` as an ASR provider option (also already in the framework).
8. Preserve all existing harness-specific functionality (character cards,
   runtime settings, memory store, TTS preset manager, profiles, UI).
9. Explore feasibility of running faster-whisper or Parakeet via audio.cpp's
   server-based architecture.
10. Clean up: delete the now-redundant local copies of providers, audio utils,
    orchestrator logic, and factory code that the framework supersedes.

### Out of Scope (for this phase)

- Replacing the browser UI.
- Changing the WebSocket wire protocol (events stay compatible).
- Adding new TTS or LLM backends beyond `audio-cpp`.
- PersonaPlex / full-duplex speech-to-speech architecture changes.

---

## Current State Assessment

### What the current Converse-AI has that the framework already provides

| Current Code (in `app/conversational_harness/`) | Framework Equivalent (`converse_framework.`) |
|---|---|
| `providers/base.py` — all protocols + dataclasses | `protocols.py` — identical contracts |
| `providers/factory.py` — builder functions + `ProviderBundle` | `registry.py` — lazy `build_provider_bundle()`, `ProviderBundle` |
| `providers/mock.py` | `providers/mock.py` |
| `providers/silero.py` | `providers/silero.py` |
| `providers/faster_whisper.py` | `providers/faster_whisper.py` |
| `providers/llamacpp.py` | `providers/llamacpp.py` (decoupled from `RuntimeSettings`) |
| `providers/kokoro_onnx.py` | `providers/kokoro_onnx.py` (decoupled from `PROJECT_ROOT`) |
| `providers/pocket_tts.py` | `providers/pocket_tts.py` |
| `providers/unavailable.py` | `providers/unavailable.py` |
| `orchestrator.py` — `ConversationOrchestrator` | `pipeline.py` — `SpeechPipeline` |
| `events.py` — `EventSink`, `HarnessEvent` | `events.py` — `EventSink`, `FrameworkEvent` (+ `QueueEventSink`, `TransportEventSink`) |
| `audio.py` — PCM/WAV conversion helpers | `audio_utils.py` |
| `audio_frames.py` — `AudioFrame`, `AudioFrameStats`, `parse_audio_frame`, etc. | `audio_utils.py` |

### What the framework has that the current codebase does NOT

| Framework Provider | Description |
|---|---|
| `whisper-cpp` (ASR) | Talks to a `whisper-server` HTTP binary. Runs Whisper GGUF models via whisper.cpp. |
| `audio-cpp` (ASR) | Talks to `audiocpp_server`. Can run Qwen3-ASR, Citrinet, Whisper GGUF models. |
| `audio-cpp` (TTS) | Talks to `audiocpp_server`. Can run Pocket TTS, Qwen3-TTS, etc. via a single server binary. |
| `WebSocketSession` | Reusable message-dispatch loop (`session.py`) — owns bundle, pipeline, collector, frame stats. |
| `Transport` protocol | `transport.py` — generic `send_event`/`receive_event` contract. |
| `cuda_utils.py` | Windows CUDA DLL discovery helper. |

### Provider registration already in the framework

```python
# From converse_framework/registry.py (bottom of file):
register_provider("asr", "whisper-cpp", ...)    # ← NEW: not in current codebase
register_provider("asr", "audio-cpp", ...)      # ← NEW: not in current codebase
register_provider("tts", "audio-cpp", ...)      # ← NEW: not in current codebase
register_provider("asr", "faster-whisper", ...) # already exists locally
register_provider("vad", "silero", ...)         # already exists locally
register_provider("llm", "llamacpp", ...)       # already exists locally
register_provider("tts", "kokoro", ...)         # already exists locally
register_provider("tts", "pocket-tts", ...)     # already exists locally
```

---

## Technical Notes: audio.cpp Integration

### How the framework's audio-cpp providers work

Both `AudioCppASRProvider` and `AudioCppTTSProvider` are **thin HTTP
clients** that talk to a user-managed `audiocpp_server` process. They do NOT
manage the server lifecycle. The server must be started separately with a config
that registers model IDs.

**TTS flow:**
1. Framework calls `stream_audio_with_progress(text)`.
2. Provider POSTs to `{base_url}/v1/audio/speech` with model, voice, text.
3. Server returns `audio/wav` bytes.
4. Provider decodes WAV → PCM s16le, yields a single `AudioChunk`.

**ASR flow:**
1. Framework calls `transcribe_audio(pcm_s16le, sample_rate)`.
2. Provider writes PCM to a temp WAV in a `shared_dir` (must be server-readable).
3. Provider POSTs to `{base_url}/v1/audio/transcriptions` with the file path.
4. Server returns JSON with `text` field.
5. Provider yields a final `TranscriptEvent`.

### Can faster-whisper run via audio.cpp?

**No, not directly.** faster-whisper uses CTranslate2 model format, while
audio.cpp uses GGUF format. They are different runtimes. However:

- **Option A:** The framework already has a standalone `FasterWhisperASRProvider`
  that runs in-process. Keep using it. No audio.cpp needed for faster-whisper.
- **Option B:** audio.cpp can run Whisper GGUF models (same models
  whisper.cpp uses). If you convert a Whisper model to GGUF or download a
  pre-converted one, the `AudioCppASRProvider` (or the simpler
  `WhisperCppASRProvider`) can run it. This gives you the audio.cpp server
  architecture but not the faster-whisper CTranslate2 speed advantage.
- **Option C:** Run both. Use faster-whisper for ASR (in-process, fastest) and
  audio-cpp for TTS (server-managed, supports multiple TTS models).

### Can Parakeet run via audio.cpp?

**Unknown — needs investigation.** Parakeet is NVIDIA's ASR model. If there is a
GGUF conversion of Parakeet available, or if `audiocpp_server` adds Parakeet
support in the future, it could work. Currently the `audiocpp_server` supports
the model archetypes registered in its build. Check the
[audio.cpp releases](https://github.com/ggerganov/audio.cpp) for the latest
supported model list.

**Recommendation:** Don't block the rebuild on Parakeet. Add the provider
plumbing first (it's zero additional code — the framework already has it), and
test with a known-working model (e.g., Whisper GGUF via audio-cpp or
whisper-cpp). Parakeet support becomes a future profile/config change.

---

## Task Breakdown

### Phase 0: Pre-flight Checks

- [ ] **Task 0.1:** Confirm `converse-framework` is installable in the project
  venv. Run `pip install converse-framework` (base, no extras). Verify
  `python -c "import converse_framework; print(converse_framework.__all__)"`
  works with only `numpy`.
- [ ] **Task 0.2:** Run existing test suite to establish baseline. Record
  pass/fail count.
- [ ] **Task 0.3:** Read `MIGRATION.md` from the framework repo for the
  reference harness migration pattern (already done as part of this plan).

### Phase 1: Install Framework and Create Compatibility Shims

**Goal:** Import the framework alongside the existing code. Existing tests still
pass. No behavioral change yet.

- [ ] **Task 1.1:** Add `converse-framework>=0.2.3` to `requirements.txt`.
  Install it in the venv.
- [ ] **Task 1.2:** Install the provider extras we actually use:
  `pip install converse-framework[silero,faster-whisper,llamacpp,kokoro,pocket-tts]`.
- [ ] **Task 1.3:** Create compatibility shim modules that re-export from the
  framework, so existing imports don't break. Files to create/update:
  - `app/conversational_harness/events.py` → re-export `EventSink`, `QueueEventSink`, `FrameworkEvent`, alias `HarnessEvent = FrameworkEvent`
  - `app/conversational_harness/audio.py` → re-export audio utils from `converse_framework.audio_utils`
  - `app/conversational_harness/audio_frames.py` → re-export `AudioFrame`, `AudioFrameStats`, `parse_audio_frame`, etc.
  - `app/conversational_harness/providers/base.py` → re-export all protocols
  - `app/conversational_harness/providers/mock.py` → re-export from framework
  - `app/conversational_harness/providers/silero.py` → re-export from framework
  - `app/conversational_harness/providers/faster_whisper.py` → re-export from framework
  - `app/conversational_harness/providers/llamacpp.py` → re-export from framework
  - `app/conversational_harness/providers/kokoro_onnx.py` → re-export from framework
  - `app/conversational_harness/providers/pocket_tts.py` → re-export from framework
  - `app/conversational_harness/providers/unavailable.py` → re-export from framework
- [ ] **Task 1.4:** Run the full test suite. Verify no regressions. Fix any import
  mismatches (e.g., the framework's `ProviderStatus` has additional fields like
  `install_hint`, `missing_extra`, `status_level` — the serialize code must handle
  them).
- [ ] **Task 1.5:** Manual smoke test: start the harness with the current
  `llamacpp-cuda-asr` profile. Verify `/api/status`, text turn, and mic turn all
  work.

### Phase 2: Replace Orchestrator with SpeechPipeline

**Goal:** Swap out `ConversationOrchestrator` for `SpeechPipeline`. This is the
largest behavioral change because the orchestrator currently does a lot.

- [ ] **Task 2.1:** Study the current `orchestrator.py` vs the framework's
  `pipeline.py`. Identify differences:
  - The current orchestrator has `seed_character_first_message()` — this stays in
    the harness as a separate function.
  - The current orchestrator directly accesses `RUNTIME_SETTINGS` for system
    prompt and sampler — the framework uses injected callables
    (`system_prompt_builder`, no built-in sampler override).
  - The framework's `SpeechPipeline` has `update_providers()` for runtime
    provider swaps — the current orchestrator doesn't.
- [ ] **Task 2.2:** Create a harness-side `system_prompt_builder` callable that
  delegates to `RUNTIME_SETTINGS.effective_system_prompt()`. This bridges the
  framework's generic interface with the harness's character card / runtime
  settings policy.
- [ ] **Task 2.3:** Wire `SpeechPipeline` into `main.py`. Replace
  `ConversationOrchestrator` instantiation. Keep the existing hook sets
  (`TTS_CANCEL_HOOKS`, `TURN_CONFIG_HOOKS`, etc.) but point them at the pipeline.
- [ ] **Task 2.4:** Adapt the llama.cpp LLM provider wiring. The framework's
  `LlamaCppProvider` does NOT accept `RuntimeSettings` directly. The current
  harness passes `set_runtime_settings()` to inject sampler overrides. The
  framework approach is to pass sampler values in the config dict at construction
  or via the LLM request messages. We'll need a harness-side adapter that builds
  the LLM config with the effective sampler on each request. (Alternative: keep a
  thin harness-side `LlamaCppProvider` subclass that reads
  `RUNTIME_SETTINGS.effective_sampler()`.)
- [ ] **Task 2.5:** Move character first-message seeding out of the orchestrator
  into a standalone harness function that reads/pokes `pipeline.messages_for_mode()`.
- [ ] **Task 2.6:** Run tests. Manual smoke test: text turn, mic turn, character
  card seeding, companion mode, barge-in.

### Phase 3: Replace Inline VAD State Machine with AudioUtteranceCollector

**Goal:** Remove the nonlocal VAD state machine from `main.py`'s WebSocket
receiver and use the framework's `AudioUtteranceCollector` instead.

- [ ] **Task 3.1:** Study the current VAD handling in `main.py` (the
  `receiver()` function's `audio.frame` branch). Map the state variables
  (`pre_buffer`, `utterance_buffer`, `recording_utterance`, etc.) to the
  collector's API.
- [ ] **Task 3.2:** Create `UtteranceCollectorConfig` from the current profile's
  `audio` and `vad` sections. The framework's config maps cleanly.
- [ ] **Task 3.3:** Instantiate `AudioUtteranceCollector` with the VAD provider,
  event sink, and an utterance callback that calls
  `pipeline.handle_audio_turn()`.
- [ ] **Task 3.4:** Refactor the WebSocket receiver to call
  `collector.ingest_frame(frame)` instead of the inline VAD state machine.
- [ ] **Task 3.5:** Wire the `pre_speech_start_hook` to handle
  `system_prompt` from the WebSocket payload (the current code reads
  `system_prompt` from the `audio.frame` payload — the collector supports this
  via the per-frame hook override).
- [ ] **Task 3.6:** Verify all rejection gates work: minimum duration, low-energy,
  utterance RMS, silence trimming. These are built into the collector.
- [ ] **Task 3.7:** Run tests. Manual smoke: mic turn, keystroke rejection,
  barge-in cancellation.

### Phase 4: Add audio.cpp Provider Support

**Goal:** Enable `audio-cpp` as ASR and TTS providers with new profiles.

- [ ] **Task 4.1:** Since the framework already registers these providers, we just
  need to add them to the harness's provider factory. Update
  `providers/factory.py` to handle `"audio-cpp"` as a provider name for ASR and
  TTS, either by delegating to the framework registry's `build_provider()` or by
  adding explicit cases.
- [ ] **Task 4.2:** Create a new profile: `profiles/audiocpp-local.json` that
  uses:
  - VAD: silero
  - ASR: `audio-cpp` with `model: "qwen3-asr"` (or `"whisper-large-v3"` if
    available in GGUF)
  - LLM: llamacpp (unchanged)
  - TTS: `audio-cpp` with `model: "pocket-tts"` or `"qwen3-tts"`
  - Include `shared_dir` config for the ASR provider
- [ ] **Task 4.3:** Add startup documentation: `docs/audio-cpp-setup.md`
  explaining how to build/run `audiocpp_server` with the desired models.
- [ ] **Task 4.4:** Update the harness's `serialize_status()` in factory.py to
  handle the additional fields in the framework's `ProviderStatus` (especially
  `install_hint`, `status_level`, `voices`, `models`). The current harness
  serialization is missing these fields.
- [ ] **Task 4.5:** Add an `install_hint` for `audio-cpp` via
  `extra_hint_for("asr", "audio-cpp")` and `extra_hint_for("tts", "audio-cpp")`.
  The framework already provides these, but the harness needs to surface them in
  `/api/status`.
- [ ] **Task 4.6:** Update `start.ps1` / `start.sh` to optionally start an
  `audiocpp_server` alongside the harness (or document it as a user-managed
  prerequisite).
- [ ] **Task 4.7:** Manual test: start `audiocpp_server` with a test model, load
  the audio.cpp profile, verify ASR transcription and TTS synthesis work.

### Phase 5: Explore faster-whisper / Parakeet via audio.cpp

**Goal:** Research-only — determine feasibility, document findings.

- [ ] **Task 5.1:** Research: can `audiocpp_server` run faster-whisper
  (CTranslate2 format) models? Likely answer: No, format mismatch. Document.
- [ ] **Task 5.2:** Research: can `audiocpp_server` run Parakeet models? Check
  the [audio.cpp GitHub](https://github.com/ggerganov/audio.cpp) for supported
  architectures. Document findings.
- [ ] **Task 5.3:** If audio.cpp supports Whisper GGUF models (it does — that's
  what whisper.cpp uses), document the conversion/capability: you can download
  Whisper GGUF models and run them via `audio-cpp` or `whisper-cpp` providers.
  This is a different runtime from faster-whisper but serves the same purpose.
- [ ] **Task 5.4:** Produce a comparison table: faster-whisper (in-process,
  CTranslate2) vs whisper-cpp (HTTP, GGUF) vs audio-cpp (HTTP, GGUF, unified
  server). Include latency, GPU support, and model availability notes.
- [ ] **Task 5.5:** Write up findings in `docs/asr-backend-comparison.md`.

### Phase 6: Cleanup — Remove Redundant Code

**Goal:** Delete the local copies of everything the framework now provides.

- [ ] **Task 6.1:** Remove the local provider implementations that are now purely
  re-export shims. The shim files themselves can stay (they're tiny and prevent
  import breakage), but delete their implementation bodies if they contain any
  non-shim code.
- [ ] **Task 6.2:** Delete the local `orchestrator.py` (or reduce it to a thin
  subclass/compatibility shim). Move remaining harness-specific logic (character
  seeding, hook management) to `main.py` or a new `harness_bridge.py`.
- [ ] **Task 6.3:** Delete or reduce `providers/factory.py`. The framework's
  `build_provider_bundle()` replaces most of it. Keep only harness-specific
  additions (TTS preset integration, profile-specific defaults).
- [ ] **Task 6.4:** Verify no dead imports remain. Run `python -m pytest`.
- [ ] **Task 6.5:** Update `README.md` to document the new dependency on
  `converse-framework` and the new `audio-cpp` and `whisper-cpp` provider options.
- [ ] **Task 6.6:** Final manual smoke test with the production profile.

---

## File Impact Summary

### Files to CREATE
- `profiles/audiocpp-local.json` — new profile for audio.cpp ASR + TTS
- `docs/audio-cpp-setup.md` — how to run `audiocpp_server`
- `docs/asr-backend-comparison.md` — faster-whisper vs whisper-cpp vs audio-cpp
- `app/conversational_harness/harness_bridge.py` — system_prompt_builder, sampler
  adapter, character seeding (extracted from orchestrator)

### Files to MODIFY
- `requirements.txt` — add `converse-framework>=0.2.3`
- `app/conversational_harness/main.py` — replace orchestrator with pipeline,
  replace inline VAD with collector, wire new providers
- `app/conversational_harness/providers/factory.py` — add audio-cpp/whisper-cpp
  cases, update serialization, delegate to framework registry
- `app/conversational_harness/events.py` — make a re-export shim
- `app/conversational_harness/audio.py` — make a re-export shim
- `app/conversational_harness/audio_frames.py` — make a re-export shim
- `app/conversational_harness/providers/base.py` — make a re-export shim
- `app/conversational_harness/providers/__init__.py` — update exports
- `app/conversational_harness/tts_runtime.py` — may need updates for framework
  `ProviderBundle.replace()` / `update_providers()` integration
- `start.ps1` / `start.sh` — optional `audiocpp_server` startup
- `README.md` — document framework dependency, new provider options
- `pytest.ini` — may need `pythonpath` adjustment if framework is installed
  editable

### Files to DELETE or HOLLOW OUT
- `app/conversational_harness/orchestrator.py` — superseded by
  `SpeechPipeline`; keep at most a thin harness adapter
- `app/conversational_harness/providers/mock.py` — implementation body
  removed, becomes pure re-export
- `app/conversational_harness/providers/silero.py` — same
- `app/conversational_harness/providers/faster_whisper.py` — same
- `app/conversational_harness/providers/llamacpp.py` — same (or keep thin
  adapter for RuntimeSettings integration)
- `app/conversational_harness/providers/kokoro_onnx.py` — same
- `app/conversational_harness/providers/pocket_tts.py` — same
- `app/conversational_harness/providers/unavailable.py` — same

---

## Risks and Mitigations

| Risk | Likelihood | Mitigation |
|---|---|---|
| Event payload shape changes break browser UI | Medium | The framework preserves the same event wire shape as the pre-extraction harness. `main.py`'s event types match. Test all event consumers. |
| `ProviderStatus` serialization mismatch | Medium | The framework's `ProviderStatus` has ADDITIONAL fields (`install_hint`, `missing_extra`, `status_level`, `voices`, `models`). Update `serialize_status()` to include them. Front-end JS may need updates if it checks field presence. |
| `LlamaCppProvider` no longer accepts `RuntimeSettings` | High | The framework provider is stateless regarding sampler overrides. Need a harness-side adapter that merges sampler params into the LLM config on each request. |
| Character card seeding breaks | Medium | `SpeechPipeline` doesn't know about character cards. Move seeding to harness-side code that pokes `messages_for_mode()`. |
| TTS preset manager integration breaks | Medium | The framework's `ProviderBundle.replace()` + `pipeline.update_providers()` provides a clean swap path. May need to adapt `tts_runtime.py`. |
| audio.cpp models not available / hard to build | Medium | Start with whisper-cpp as a simpler fallback (only needs `whisper-server` binary, widely available). Document audio.cpp as experimental. |
| Test suite breaks due to import changes | Low | Compat shims prevent import breakage. Tests that import from now-shimmed modules should still work. |

---

## Verification Plan

### Automated

```powershell
# After each phase:
python -m pytest -v

# Check framework import is clean with only base deps:
python -c "import converse_framework; print(len(converse_framework.__all__))"

# Check all provider extras are importable:
python -c "from converse_framework.providers.audio_cpp import AudioCppASRProvider, AudioCppTTSProvider"
python -c "from converse_framework.providers.whisper_cpp import WhisperCppASRProvider"
```

### Manual

1. Start harness with current `llamacpp-cuda-asr` profile. Verify text turn,
   mic turn, barge-in, TTS playback.
2. Switch to a mock profile (`mock-local.json`). Verify that works.
3. Start `audiocpp_server` with a test model. Load `audiocpp-local.json`
   profile. Verify ASR and TTS via audio.cpp.
4. Verify `/api/status` shows correct provider info, especially the new
   `status_level`, `install_hint`, and `voices`/`models` fields.
5. Verify character card loading still works.
6. Verify runtime settings (sampler overrides) still apply.
7. Verify companion mode still works.

---

## Out of Scope / Future Work

- **Parakeet integration:** Depends on audio.cpp adding Parakeet support.
  Document as a follow-up investigation.
- **PersonaPlex / full-duplex:** Separate architecture change, not part of this
  rebuild.
- **New audio.cpp model archetypes (e.g., voice conversion):** The framework's
  audio-cpp provider already handles the OpenAI-compatible speech and
  transcription endpoints. New endpoints would need provider updates.
- **Replacing the browser UI with framework JS helpers:** The framework ships
  `mic-frame-sender.js`, `tts-audio-player.js`, and `browser-voice-client.js`.
  We could optionally adopt these, but it's not required for the backend
  rebuild.

---

## Discussion Points for the User

1. **faster-whisper vs audio.cpp for ASR:** faster-whisper runs in-process with
   CTranslate2 (fast, CUDA-optimized). audio.cpp is a separate server process
   using GGUF models (architecturally cleaner, but potentially different
   latency characteristics). Do you want to keep both as options, or migrate
   entirely to one?

2. **Parakeet:** There's no evidence that `audiocpp_server` currently supports
   Parakeet models. The audio.cpp project supports specific model archetypes
   (Whisper, Qwen3-ASR, Citrinet for ASR; Pocket TTS, Qwen3-TTS for TTS).
   Parakeet would need explicit support added to audio.cpp. Should we table
   Parakeet for now and focus on the working providers?

3. **`WebSocketSession` helper:** The framework ships a `WebSocketSession` class
   that handles message dispatch. Adopting it would replace more of `main.py`'s
   receiver logic. Worth doing, or keep our own receiver to minimize churn?

4. **Profile migration:** Should we keep the existing profile format and just
   add new `provider` values (`"audio-cpp"`, `"whisper-cpp"`), or restructure
   profiles to match the framework's expected config shape more closely?

5. **Aggressiveness of cleanup:** Phase 6 removes local provider implementations
   entirely. This is clean but means we lose the ability to patch providers
   locally. The alternative is to keep thin subclasses that inherit from
   framework providers, allowing harness-specific overrides. Preference?
