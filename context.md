# Code Context: Modularity Analysis

## Files Retrieved
1. `app/conversational_harness/providers/base.py` (1-103) — Protocol contracts + data models
2. `app/conversational_harness/orchestrator.py` (1-309) — Turn orchestration, TTS chunking, QueueEventSink
3. `app/conversational_harness/main.py` (1-550+) — FastAPI app + WebSocket handler with VAD pipeline
4. `app/conversational_harness/events.py` (1-25) — EventSink abstract class + HarnessEvent
5. `app/conversational_harness/config.py` (1-43) — HarnessConfig + profile loading
6. `app/conversational_harness/audio.py` (1-72) — Pure audio conversion utilities
7. `app/conversational_harness/audio_frames.py` (1-143) — Audio frame parsing & PCM analysis
8. `app/conversational_harness/providers/factory.py` (1-126) — Provider builder registry
9. `app/conversational_harness/runtime_settings.py` (1-350+) — Character cards, RuntimeSettings, MemoryStore
10. `app/conversational_harness/tts_runtime.py` (1-180+) — TTS preset manager
11. `app/conversational_harness/providers/mock.py` (1-116) — Mock providers (reference consumer)
12. `app/conversational_harness/providers/silero.py` (1-124) — Silero VAD provider
13. `app/conversational_harness/providers/faster_whisper.py` (1-170+) — faster-whisper ASR provider
14. `app/conversational_harness/providers/llamacpp.py` (1-160+) — llama.cpp LLM provider
15. `app/conversational_harness/providers/unavailable.py` (1-77) — Unified unavailable fallback
16. `app/conversational_harness/launch.py` (1-300+) — CLI launcher, doctor checks, Cloudflare tunnel

---

## 1. `providers/base.py` — Provider Protocols & Data Models

**Path:** `app/conversational_harness/providers/base.py` (lines 1-103)

**Imports:** Only `__future__`, `dataclasses`, `typing`. Zero app-level imports.

**Key types:**
- `ProviderCapabilities` (frozen dataclass, line 8): `supports_partials`, `supports_streaming_tts`, `supports_barge_in`, `requires_gpu`, `languages`
- `ProviderStatus` (frozen dataclass, line 14): `name`, `kind`, `ready`, `message`, `capabilities`, etc.
- `TranscriptEvent` (frozen dataclass, line 25): `text`, `final`
- `AudioChunk` (frozen dataclass, line 29): `data`, `mime_type`, `sample_rate`, `channels`, `encoding`, `duration_ms`, `final`
- `VADEvent` (frozen dataclass, line 37): `type`, `probability`, `audio_ms`
- `ProgressCallback` (type alias, line 42): `Callable[[str, dict], Awaitable[None]]`
- `VADProvider` (Protocol, line 45): `status`, `check_status()`, `process_frame(frame)`, `unload()`
- `ASRProvider` (Protocol, line 57): `status`, `check_status()`, `load()`, `transcribe_text_input()`, `transcribe_audio()`, `unload()`
- `LLMProvider` (Protocol, line 74): `status`, `check_status()`, `stream_response(messages)`
- `TTSProvider` (Protocol, line 84): `status`, `check_status()`, `load()`, `unload()`, `stream_audio()`, `stream_audio_with_progress()`

**Verdict:** Fully self-contained. No dependency on any other harness module. Could move to a standalone `conversational-ai-interfaces` package today.

**Extraction effort: LOW** — Copy the file. No refactoring needed.

**Note:** The `process_frame(frame)` parameter on `VADProvider` is untyped. In practice providers receive `AudioFrame` from `audio_frames.py`. If extracted, either make `AudioFrame` a shared base type or keep it duck-typed.

---

## 2. `orchestrator.py` — Conversation Orchestrator

**Path:** `app/conversational_harness/orchestrator.py` (lines 1-309)

**Imports from app modules:**
- `conversational_harness.events` → `EventSink` (abstract, line 19)
- `conversational_harness.providers.factory` → `ProviderBundle` (line 20)
- `conversational_harness.runtime_settings` → `RuntimeSettings`, `MemoryStore` (TYPE_CHECKING only, line 24)

**Key classes:**
- `TurnState` (dataclass, line 28): messages, active TTS tasks, system_prompt, turn_id
- `ConversationOrchestrator` (class, line 37): Full turn lifecycle — text turns, audio turns, continue, LLM streaming + TTS chunking
- `QueueEventSink` (class, line 295): Concrete `EventSink` wrapping an `asyncio.Queue`
- `elapsed_ms()` / `should_flush_tts()` (module-level, lines 305-309)

**What it does vs what it depends on:**
- Orchestrator takes `EventSink` (abstract) + `ProviderBundle` → no FastAPI/WebSocket dependency
- Uses only async primitives (`asyncio.create_task`, `asyncio.gather`, `asyncio.CancelledError`)
- `_stream_llm_and_tts()` (line 210) calls `providers.llm.stream_response()` and `providers.tts.stream_audio_with_progress()` through protocol interfaces
- `_effective_system_prompt()` (line 272) calls `runtime_settings.effective_system_prompt()` — but behind TYPE_CHECKING guard
- `QueueEventSink` lives here but is only used by `main.py`; could stay or move into `events.py`

**Verdict:** Reusable without FastAPI/WebSocket. Depends on `EventSink` abstraction (from `events.py`) + `ProviderBundle` + optional `RuntimeSettings`. Could be extracted alongside `events.py` and `base.py`.

**Extraction effort: LOW-MEDIUM** — Must bundle with `events.py` and `base.py` (Protocols). `ProviderBundle` is a thin dataclass from `factory.py` that could be moved into the extracted layer. `RuntimeSettings` reference is TYPE_CHECKING-only so no hard import needed, but the runtime_settings integration points need documentation.

---

## 3. `main.py` — FastAPI App + WebSocket Handler (VAD Pipeline)

**Path:** `app/conversational_harness/main.py` (lines 1-550+)

**This is the critical bottleneck.** The VAD/audio processing pipeline is deeply interleaved with WebSocket framing inside the `receiver()` inner function.

**Architecture:**
- Lines 1-40: Imports — depends on FastAPI, `audio_frames`, `config`, `orchestrator`, `factory`, `runtime_settings`, `tts_runtime`
- Lines 42-63: Module-level globals (`app`, `BASE_CONFIG`, `TTS_MANAGER`, `RUNTIME_SETTINGS`, `MEMORY_STORE`, hook sets)
- Lines 145-205: REST endpoints (`/api/status`, `/api/tts/*`, `/api/settings`, `/api/companion/*`)
- Lines 207-240: Character upload endpoints
- Lines 280-310: Broadcast helpers (`broadcast`, `broadcast_providers_status`, `broadcast_settings`, `cancel_active_tts`, `refresh_turn_config`, `seed_character_first_messages`)

**WebSocket handler** (`websocket_events`, lines 315-550+):
- Lines 315-350: Setup — create providers, queue, `QueueEventSink`, config extraction, `orchestrator` instance
- Lines 350-360: Register hook callbacks (`TTS_CANCEL_HOOKS`, `TURN_CONFIG_HOOKS`, etc.)
- Lines 362-390: `warm_asr_on_connect()` — ASR preload
- Lines 392-415: `sender()` — reads from `asyncio.Queue`, pushes JSON over WebSocket
- **Lines 418-550+: `receiver()` — THE EXTRACTION TARGET**

### VAD Pipeline Inside `receiver()` (lines 418-550+)

The receiver handles 8 message types. The VAD/audio pipeline lives in `message_type == "audio.frame"` (approx lines 460-550):

| Lines | Logic | Coupling |
|-------|-------|----------|
| 462-466 | `parse_audio_frame(payload, audio_stats)` + error emission | Tight — emits over sink, uses `audio_stats` from outer scope |
| 467-468 | `pre_buffer.append(frame.data)` — pre-speech buffering | Tight — `pre_buffer` is `nonlocal` deque |
| 469-473 | `utterance_buffer.extend()` + max utterance check | Tight — `recording_utterance` nonlocal bool |
| 474-475 | `audio_stats.update(frame)` → emit `audio.input_level` | Tight — sink emit |
| 477-480 | `providers.vad.process_frame(frame)` | OK — through protocol interface, but triggers side effects |
| 482 | `vad_event.type == "vad.speech_start"` | **Barge-in logic starts here** |
| 484-494 | `recording_mode` set, `cancel_tts("vad_barge_in")`, `utterance_buffer.clear()`, drain `pre_buffer` into `utterance_buffer`, `recording_utterance = True` | All state is nonlocal to `receiver()` |
| 497-502 | `vad_event.type == "vad.speech_end"` | **Noise rejection chain starts here** |
| 503 | `pcm = bytes(utterance_buffer)` | Finalize utterance |
| 505-515 | `min_speech_duration_ms` gate | Duration check, emits `vad.speech_rejected` |
| 516-528 | `reject_low_energy_rms` + `reject_low_energy_max_duration_ms` gate | RMS check on short utterances |
| 529-541 | `reject_utterance_rms` gate | Whole-utterance floor |
| 542-555 | `trim_pcm16_silence()` edge trimming | Calls `audio_frames.trim_pcm16_silence` |
| 556-565 | Launch `orchestrator.handle_audio_turn(pcm, sample_rate, mode)` | Final turn dispatch |

**Total VAD pipeline state** (all `nonlocal` to `receiver()`):
- `recording_utterance: bool`
- `recording_mode: str`
- `pre_buffer: deque[bytes]`
- `utterance_buffer: bytearray`
- `active_turn: asyncio.Task | None`

**Config values extracted from outer scope:**
- `min_speech_duration_ms` (line 331)
- `reject_low_energy_rms` (line 332)
- `reject_low_energy_max_duration_ms` (line 333-335)
- `reject_utterance_rms` (line 336)
- `trim_silence_rms` (line 337)
- `trim_silence_frame_ms` (line 338-340)
- `pre_speech_frames` (line 341)
- `max_utterance_frames` (line 342)
- `bytes_per_ms` (line 343)
- `expected_frame_bytes` (line 344)

**What would need factoring out:**
A new class, e.g. `AudioPipeline` or `UtteranceCollector`, that encapsulates:
- Pre-buffering (`pre_buffer` with configurable `pre_speech_frames`)
- Utterance collection (`utterance_buffer` with configurable `max_utterance_frames`)
- The noise rejection chain (min duration, low-energy, utterance RMS, trim silence)
- VAD event handling (speech_start → drain pre-buffer, speech_end → finalize + filter)
- Emits events through an injected `EventSink`
- Dispatches completed utterances to orchestrator via a callback

**Verdict:** Deeply interleaved. The `receiver()` is ~130 lines of VAD/audio logic mixed with WebSocket message framing, turn management, and hook registration. This is the primary extraction target.

**Extraction effort: HIGH** — Requires creating a separate class with clear boundaries, extracting all nonlocal state, threading config values as constructor params, and providing a clean callback interface. The rest of the WebSocket handler (text turns, continue, system_prompt updates, conversation clear) is already well-delegated to `orchestrator`.

---

## 4. `config.py` — Profile Configuration

**Path:** `app/conversational_harness/config.py` (lines 1-43)

**Imports:** `json`, `os`, `dataclasses`, `pathlib`. Zero app imports.

**Key types:**
- `PROJECT_ROOT` (line 9): `Path(__file__).resolve().parents[2]` — hard-coded to app package layout
- `DEFAULT_PROFILE` (line 10): `PROJECT_ROOT / "profiles" / "llamacpp-cuda-asr.json"`
- `HarnessConfig` (frozen dataclass, line 13): `path`, `raw` dict, `.name` property, `.section(name)` method
- `resolve_profile_path(value)` (line 24): Handles env var `HARNESS_PROFILE` + path resolution
- `load_config(value)` (line 35): Reads JSON file → `HarnessConfig`

**Verdict:** Nearly pure. Only coupling is `PROJECT_ROOT` being computed from `__file__`. If extracted, the root path should be injected rather than derived from package layout.

**Extraction effort: LOW** — Replace `PROJECT_ROOT` with an explicit parameter. The `HarnessConfig` dataclass and load logic are otherwise dependency-free.

---

## 5. `events.py` — Event System

**Path:** `app/conversational_harness/events.py` (lines 1-25)

**Imports:** `time`, `dataclasses`, `typing`. Zero app imports.

**Key types:**
- `HarnessEvent` (dataclass, line 9): `type`, `payload`, `ts`
- `EventSink` (class, line 17): Abstract `emit(event_type, **payload)` method

**Verdict:** Minimal and abstract. No FastAPI, no WebSocket, no app-level types. Works with any event bus.

**Extraction effort: LOW** — Copy the file. This is the ideal "framework" module.

**Note:** `QueueEventSink` (concrete implementation) currently lives in `orchestrator.py` (line 295). It could be moved here or kept separate.

---

## 6. `audio.py` — Audio Conversion Utilities

**Path:** `app/conversational_harness/audio.py` (lines 1-72)

**Imports:** `math`, `struct`, `wave`, `io.BytesIO`, `numpy`. Zero app imports.

**Key functions:**
- `make_tone_wav()` (line 11): Generates WAV tone for mock TTS
- `pcm_s16le_to_float32()` (line 30): PCM bytes → float32 numpy array
- `float_audio_to_wav_bytes()` (line 37): Float audio → WAV bytes
- `float_audio_to_pcm_s16le_bytes()` (line 50): Float audio → PCM s16le bytes
- `tensor_or_array_to_numpy()` (line 60): Extract numpy array from tensor/array

**Verdict:** Pure utility module. Zero app dependencies. Only external dep is `numpy`.

**Extraction effort: LOW** — Copy as-is. No refactoring needed.

---

## 7. `audio_frames.py` — Audio Frame Parsing & PCM Analysis

**Path:** `app/conversational_harness/audio_frames.py` (lines 1-143)

**Imports:** `base64`, `time`, `dataclasses`, `typing`, `numpy`. Zero app imports.

**Key types/functions:**
- `SUPPORTED_ENCODING` = `"pcm_s16le"` (line 10)
- `AudioFrame` (frozen dataclass, line 13): `data`, `sequence`, `sample_rate`, `channels`, `frame_ms`, `encoding`
- `AudioFrameStats` (dataclass, line 22): Tracks frame sequences, dropped frames, emits periodic metrics
- `parse_audio_frame()` (line 42): Validates + decodes WebSocket audio frame payload → `AudioFrame`
- `compute_pcm16_level()` (line 107): RMS + peak from PCM bytes
- `trim_pcm16_silence()` (line 119): Edge-trim silent frames

**Verdict:** Pure utility module. `parse_audio_frame()` is the bridge between WebSocket JSON and the VAD pipeline, but it has no WebSocket dependency itself — it just validates a dict. `AudioFrameStats` emits metrics dicts but doesn't depend on the event system.

**Extraction effort: LOW** — Copy as-is. The `AudioFrame` dataclass could live alongside the protocols in `base.py`, but current file has no coupling that would prevent extraction.

---

## 8. `providers/factory.py` — Provider Builder Registry

**Path:** `app/conversational_harness/providers/factory.py` (lines 1-126)

**Imports from app modules:**
- `conversational_harness.config` → `HarnessConfig` (line 6)
- `conversational_harness.providers.base` → all protocols + `ProviderStatus` (line 7)
- `conversational_harness.providers.faster_whisper` (line 8)
- `conversational_harness.providers.kokoro_onnx` (line 9)
- `conversational_harness.providers.llamacpp` (line 10)
- `conversational_harness.providers.mock` (line 11)
- `conversational_harness.providers.pocket_tts` (line 12)
- `conversational_harness.providers.silero` (line 13)
- `conversational_harness.providers.unavailable` (line 14)

**Key types:**
- `ProviderBundle` (dataclass, line 18): Holds `vad`, `asr`, `llm`, `tts` providers
- `build_vad()`, `build_asr()`, `build_llm()`, `build_tts()` (lines 68-108): If/elif dispatch
- `TTS_PROVIDER_BUILDERS` dict (line 103): Plugin-style registry for TTS only
- `build_provider_bundle()` (line 111): Flows audio sample_rate into vad config

**Verdict:** The factory ties concrete providers to the harness. VAD/ASR/LLM builders use hardcoded if/elif chains; TTS uses a dict registry (better pattern). `ProviderBundle` is a thin dataclass but is imported by `orchestrator.py`.

**Extraction effort: MEDIUM** — To make framework-quality, replace hardcoded if/elif with a pluggable registry (like the TTS dict). `ProviderBundle` could move into the framework layer. The factory's knowledge of concrete providers is appropriate — it's an assembly module.

---

## 9. `runtime_settings.py` — Character Cards & Runtime Settings

**Path:** `app/conversational_harness/runtime_settings.py` (lines 1-350+)

**Imports from app modules:**
- `conversational_harness.config` → `PROJECT_ROOT` (line 11)

**Key types:**
- `CharacterCard` (dataclass, line 42): All TavernAI card fields, `build_system_prompt()`, `first_message()`
- `CompanionSettings` (dataclass, line 99): user_name, ai_name, sampler merge logic
- `RuntimeSettings` (dataclass, line 180): Central settings singleton — sampler three-tier merge, system prompt assembly, character management, to_dict/to_json/apply_patch
- `MemoryStore` (class, line 317): File-backed memory with append_summary
- `parse_character_json()`, `parse_character_png()` (lines 78, 82): Hand-written PNG chunk parser
- `load_runtime_settings()`, `save_runtime_settings()` (lines 289, 309)

**Coupling to config:** Only `PROJECT_ROOT` for computing `SETTINGS_PATH` and `MEMORY_PATH`. If these paths were injected, the module would be standalone.

**Verdict:** The `CharacterCard` parser (including the hand-written PNG chunk scanner) and `RuntimeSettings` three-tier merge logic are solid framework pieces. The `MemoryStore` is a simple file-backed store. Only coupling is `PROJECT_ROOT` constant.

**Extraction effort: LOW-MEDIUM** — Parameterize the file paths (`SETTINGS_PATH`, `MEMORY_PATH`) so they're injected rather than computed from `PROJECT_ROOT`.

---

## 10. `tts_runtime.py` — TTS Preset Manager

**Path:** `app/conversational_harness/tts_runtime.py` (lines 1-180+)

**Imports from app modules:**
- `conversational_harness.config` → `DEFAULT_PROFILE`, `HarnessConfig`, `PROJECT_ROOT` (line 9)
- `conversational_harness.providers.factory` → `build_tts`, `serialize_status` (line 12)

**Key types:**
- `TTSPreset` (frozen dataclass, line 13): id, label, provider, tts config, turn config, voices
- `load_tts_presets()` (line 33): Reads `profiles/tts-presets.json`
- `TTSRuntimeManager` (class, line 55): Preset selection, voice overrides, provider rebuild on change, `merged_profile_raw()`, `current_turn_config()`

**Verdict:** Moderately coupled. Knows about profile file layout (`profiles/tts-presets.json`, `DEFAULT_PROFILE`), uses `build_tts` from factory to construct providers. The preset selection/voice override logic is generic; the file I/O and config path knowledge is harness-specific.

**Extraction effort: MEDIUM** — The runtime management (preset switching, voice overrides, provider rebuilding) is reasonable framework material. The file paths and `build_tts` dependency tie it to the harness. Would need path injection and a provider builder callback.

---

## Summary Table

| Module | App Imports | Standalone? | Effort | Notes |
|--------|-------------|-------------|--------|-------|
| `providers/base.py` | None | ✅ Yes | LOW | Pure protocols + dataclasses. Zero deps. |
| `events.py` | None | ✅ Yes | LOW | Abstract EventSink. Zero deps. |
| `audio.py` | None (only numpy) | ✅ Yes | LOW | Pure utility functions. |
| `audio_frames.py` | None (only numpy) | ✅ Yes | LOW | Pure utility. AudioFrame could move to base. |
| `config.py` | None | ✅ Almost | LOW | `PROJECT_ROOT` hard-coded. Inject it. |
| `orchestrator.py` | `events`, `factory`, `runtime_settings` (TYPE_CHECKING) | ✅ Reusable | LOW-MEDIUM | Needs `base.py` + `events.py`. `RuntimeSettings` optional. |
| `runtime_settings.py` | `config` (PROJECT_ROOT only) | ✅ Almost | LOW-MEDIUM | Inject file paths. CharacterCard parser is standalone. |
| `factory.py` | `config`, `base`, all concrete providers | ⚠️ Assembly | MEDIUM | Replace if/elif with registry. Bundle is thin. |
| `tts_runtime.py` | `config`, `factory` | ⚠️ Partial | MEDIUM | Inject paths + builder callback. |
| `main.py` (VAD pipeline) | Everything | ❌ No | HIGH | ~130 lines of VAD/audio logic interleaved with WebSocket. Primary extraction target. |
| Concrete providers | `base`, `audio`, `audio_frames` | ✅ Through interfaces | LOW | Mock/Silero/Whisper/llamacpp/Kokoro/Pocket — all implement protocols, import only `base` + utilities. |

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────┐
│  main.py (FastAPI + WebSocket)                          │
│  ┌───────────────────────────────────────────────────┐  │
│  │  websocket_events() handler                       │  │
│  │  ┌─────────────┐  ┌────────────────────────────┐  │  │
│  │  │ sender()    │  │ receiver()                 │  │  │
│  │  │ (queue→WS)  │  │  ┌──────────────────────┐  │  │  │
│  │  │             │  │  │ VAD PIPELINE (mud)   │  │  │  │
│  │  │             │  │  │ • pre_buffer         │  │  │  │
│  │  │             │  │  │ • utterance_buffer   │  │  │  │
│  │  │             │  │  │ • noise rejection    │  │  │  │
│  │  │             │  │  │ • silence trimming   │  │  │  │
│  │  │             │  │  │ • barge-in cancel    │  │  │  │
│  │  │             │  │  └──────┬───────────────┘  │  │  │
│  │  │             │  │         │ dispatch          │  │  │
│  │  └─────────────┘  │  ┌──────▼───────────────┐  │  │  │
│  │                    │  │ text/continue/clear  │  │  │  │
│  │                    │  └──────┬───────────────┘  │  │  │
│  │                    └─────────┼───────────────────┘  │  │
│  └──────────────────────────────┼──────────────────────┘  │
│                                 │                          │
│  REST endpoints                 │                          │
│  /api/status, /api/settings,    │                          │
│  /api/tts/*, /api/companion/*   │                          │
└─────────────────────────────────┼──────────────────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │  orchestrator.py          │
                    │  ConversationOrchestrator │
                    │  • Turn lifecycle         │
                    │  • LLM streaming + TTS    │
                    │  • QueueEventSink         │
                    └──────┬──────────┬─────────┘
                           │          │
              ┌────────────▼──┐  ┌───▼──────────────┐
              │ events.py     │  │ factory.py        │
              │ EventSink     │  │ ProviderBundle    │
              │ HarnessEvent  │  │ build_*()         │
              └───────────────┘  └───┬───────────────┘
                                     │
                         ┌───────────▼──────────────┐
                         │ providers/base.py        │
                         │ VAD/ASR/LLM/TTS Protocols│
                         │ TranscriptEvent, etc.    │
                         └──────────────────────────┘
```

**Data flow:**
1. Browser mic → WebSocket `audio.frame` messages → `main.py receiver()`
2. `receiver()` parses frames, feeds to `providers.vad.process_frame()`
3. VAD events trigger pre-buffer drain / utterance finalization
4. Noise rejection gates filter utterances
5. Clean utterance → `orchestrator.handle_audio_turn(pcm, sample_rate, mode)`
6. Orchestrator → `providers.asr.transcribe_audio()` → `providers.llm.stream_response()` → `providers.tts.stream_audio_with_progress()`
7. All emits → `EventSink` → `asyncio.Queue` → `sender()` → WebSocket JSON → browser

---

## Start Here

Open `app/conversational_harness/main.py` at line ~418 (the `receiver()` function). This is where the VAD pipeline lives. The extraction goal is to create a new class (e.g., `AudioUtteranceCollector` in a new `pipeline.py` or similar) that absorbs lines 462-565.

**Key sections within receiver():**
- Lines 315-350: Setup (config extraction — these values would become constructor params)
- Lines 462-476: Audio frame parsing + buffer management
- Lines 482-502: VAD state transitions (speech_start / speech_end)
- Lines 503-555: Noise rejection cascade (min duration, low energy, utterance RMS, trim silence)
- Lines 556-565: Turn dispatch to orchestrator

**Secondary files to open after main.py:**
- `app/conversational_harness/orchestrator.py` line 37 (`ConversationOrchestrator.__init__`) — to understand what the refactored pipeline would call
- `app/conversational_harness/audio_frames.py` line 42 (`parse_audio_frame`) — the bridge between WebSocket payloads and AudioFrame objects
