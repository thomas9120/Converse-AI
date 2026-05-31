# Conversational AI Harness — Architecture Report

> Auto-generated from source analysis. Date: 2026-05-31

---

## Table of Contents

1. [Overview](#overview)
2. [Project Structure](#project-structure)
3. [Architecture Diagram](#architecture-diagram)
4. [Core Components](#core-components)
5. [Provider System](#provider-system)
6. [Event System & Real-Time Communication](#event-system--real-time-communication)
7. [Data Flow Pipeline](#data-flow-pipeline)
8. [Barge-In Flow](#barge-in-flow)
9. [Character Card System](#character-card-system)
10. [Runtime Settings Architecture](#runtime-settings-architecture)
11. [TTS Runtime Manager](#tts-runtime-manager)
12. [Frontend Architecture](#frontend-architecture)
13. [Profile & Configuration System](#profile--configuration-system)
14. [Startup & Operational Scripts](#startup--operational-scripts)
15. [Test Suite](#test-suite)
16. [Key Design Patterns](#key-design-patterns)

---

## Overview

The Conversational AI Harness is a **local, modular, real-time voice interaction system** that chains four AI components into a conversational pipeline:

```
Microphone → VAD → ASR → LLM → TTS → Speaker
```

It runs entirely on localhost (no cloud API required), uses WebSocket for real-time bidirectional streaming, and exposes a browser-based UI at `http://localhost:7860`. The stack is Python backend (FastAPI + uvicorn) with a vanilla JS/HTML/CSS frontend.

**Design philosophy**: Every component sits behind a provider interface so VAD, ASR, LLM, and TTS can be swapped independently. Profiles define which providers and models are active.

---

## Project Structure

```
Conversational-AI-Harness/
├── app/
│   ├── conversational_harness/          # Python backend package
│   │   ├── __init__.py
│   │   ├── main.py                      # FastAPI app, WebSocket, API routes, singletons
│   │   ├── orchestrator.py              # ConversationOrchestrator, turn lifecycle, TTS chunking
│   │   ├── config.py                    # HarnessConfig dataclass, profile loading
│   │   ├── events.py                    # HarnessEvent, EventSink base class
│   │   ├── audio.py                     # PCM conversion utilities
│   │   ├── audio_frames.py             # AudioFrame parsing, level metering
│   │   ├── runtime_settings.py         # RuntimeSettings singleton, CharacterCard, sampler merge
│   │   ├── tts_runtime.py              # TTSRuntimeManager, TTSPreset, hot-swap
│   │   ├── doctor.py                    # Diagnostic/health checks
│   │   └── providers/
│   │       ├── __init__.py              # Re-exports build_providers
│   │       ├── base.py                  # Protocol definitions (VAD/ASR/LLM/TTS)
│   │       ├── factory.py              # ProviderBundle, build_* factory functions
│   │       ├── silero.py               # Silero VAD (ONNX on CPU)
│   │       ├── faster_whisper.py        # Faster-Whisper ASR (CTranslate2)
│   │       ├── llamacpp.py             # llama.cpp LLM (OpenAI-compatible HTTP)
│   │       ├── pocket_tts.py           # Pocket TTS (in-process, CPU)
│   │       ├── kokoro_onnx.py          # Kokoro ONNX TTS (CPU, Misaki G2P)
│   │       ├── mock.py                 # Mock providers for testing
│   │       └── unavailable.py          # Fallback stub for missing providers
│   └── static/                          # Frontend
│       ├── index.html                   # Single-page app shell
│       ├── app.js                       # Client logic (~1086 lines)
│       └── styles.css                   # Light/dark theme CSS
├── profiles/                            # Configuration profiles
│   ├── llamacpp-cuda-asr.json           # Default: CUDA ASR + Pocket TTS
│   ├── llamacpp-kokoro-onnx.json        # CPU ASR + Kokoro TTS
│   ├── llamacpp-local.json             # CPU ASR + Pocket TTS
│   ├── mock-local.json                  # All mock providers
│   └── tts-presets.json                 # TTS preset definitions & voice lists
├── tests/                               # Test suite
├── docs/                                # Documentation (this file)
├── requirements.txt                     # Python dependencies
├── user_settings.json                   # Persisted runtime settings
├── start.ps1 / start.sh                # Launch scripts
├── stop.ps1 / stop.sh                  # Shutdown scripts
├── install.ps1 / install.sh            # Installation scripts
├── doctor.ps1 / doctor.sh / doctor.py  # Diagnostic scripts
├── update.ps1 / update.sh             # Update scripts
├── AGENTS.md                           # Agent guidelines
├── README.md                           # User-facing docs
└── TODO.md                             # Development tracking
```

---

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                        Browser Client                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌───────────────┐   │
│  │Mic Capture│  │WebSocket │  │Web Audio │  │Settings UI    │   │
│  │ScriptProc │  │Handler   │  │Playback  │  │(sampler, char)│  │
│  └─────┬─────┘  └────┬─────┘  └────▲─────┘  └───────┬───────┘   │
│        │              │             │                │           │
│        │  audio.frame │             │ tts.audio      │ settings  │
│        │  user.text   │             │ asr.transcript  │ changes  │
│        └──────────────┼─────────────┼────────────────┘           │
│                       │             │                             │
└───────────────────────┼─────────────┼─────────────────────────────┘
                        │             │
                   WebSocket /ws/events
                   REST /api/*
                        │             │
┌───────────────────────▼─────────────▼─────────────────────────────┐
│                    main.py (FastAPI + uvicorn)                     │
│                                                                    │
│  Singletons:                                                       │
│    BASE_CONFIG       ← HarnessConfig from profile                  │
│    TTS_MANAGER       ← TTSRuntimeManager (preset hot-swap)        │
│    RUNTIME_SETTINGS  ← RuntimeSettings (sampler, char, names)     │
│    ACTIVE_QUEUES     ← set[asyncio.Queue] for broadcast           │
│                                                                    │
│  Hook Sets:                                                        │
│    TTS_CANCEL_HOOKS     → orchestrator.cancel_tts()               │
│    TURN_CONFIG_HOOKS    → orchestrator.update_tts_provider()      │
│    CHARACTER_SEED_HOOKS → orchestrator.seed_character_first_msg()  │
│                                                                    │
│  ┌─────────────────────────────────────────────────────────┐      │
│  │           ConversationOrchestrator                      │      │
│  │                                                         │      │
│  │  handle_audio_turn(pcm) → VAD → ASR → LLM → TTS       │      │
│  │  handle_text_turn(text)          → LLM → TTS            │      │
│  │  handle_continue()               → LLM → TTS          │      │
│  │  cancel_tts(reason)               → cancel active TTS  │      │
│  │                                                         │      │
│  │  _stream_llm_and_tts():                                 │      │
│  │    async for token from LLM:                            │      │
│  │      sentence_buffer += token                           │      │
│  │      if should_flush_tts(): start TTS chunk             │      │
│  │                                                         │      │
│  │  QueueEventSink → asyncio.Queue → WS sender            │      │
│  └──────────────────────┬──────────────────────────────────┘      │
│                         │                                          │
│  ┌──────────────────────▼──────────────────────────────────┐      │
│  │              ProviderBundle                               │      │
│  │  vad: SileroVADProvider    (ONNX, CPU)                   │      │
│  │  asr: FasterWhisperASRProvider (CTranslate2, CPU/CUDA)  │      │
│  │  llm: LlamaCppProvider     (HTTP, OpenAI-compat)        │      │
│  │  tts: PocketTTSProvider | KokoroOnnxProvider             │      │
│  └─────────────────────────────────────────────────────────┘      │
│                                                                    │
│  TTSRuntimeManager   ← hot-swaps tts in ProviderBundle            │
│  RuntimeSettings     ← three-tier sampler, layered prompt         │
└────────────────────────────────────────────────────────────────────┘
```

---

## Core Components

### main.py — Central Wiring

The heart of the application. Responsibilities:

- **FastAPI app** with CORS middleware
- **WebSocket endpoint** (`/ws/events`) with bidirectional `receiver()`/`sender()` coroutines
- **REST API routes**: `/api/status`, `/api/settings`, `/api/tts/*`, `/api/settings/character/upload`
- **Module-level singletons**: `BASE_CONFIG`, `TTS_MANAGER`, `RUNTIME_SETTINGS`, `ACTIVE_QUEUES`
- **Hook sets**: `TTS_CANCEL_HOOKS`, `TURN_CONFIG_HOOKS`, `CHARACTER_SEED_HOOKS` — allow per-WS orchestrators to react to global state
- **Utterance buffering**: accumulates audio frames in `utterance_buffer` with pre-speech ring buffer
- **Min speech duration gate**: rejects short utterances (< `min_speech_duration_ms`) as noise

### orchestrator.py — Conversation Lifecycle

Manages a single conversation session per WebSocket connection.

- **TurnState**: tracks messages, active TTS tasks, cancellation flags
- **handle_audio_turn(pcm, sample_rate)**: VAD check → ASR transcription → LLM stream → chunked TTS
- **handle_text_turn(text)**: skip VAD/ASR → LLM stream → chunked TTS
- **handle_continue()**: keeps last assistant message as prefix, asks LLM to continue
- **cancel_tts(reason)**: cancels all active TTS asyncio tasks, awaits cancellation, emits `tts.cancelled`
- **QueueEventSink**: bridges `EventSink.emit()` to `asyncio.Queue` for WS sender

### config.py — Configuration Loading

- `HarnessConfig`: wraps a profile dict, provides `.section(key)` accessor
- `load_config(profile_name)`: reads JSON from `profiles/` directory
- `DEFAULT_PROFILE = "llamacpp-cuda-asr"` — default profile constant

### events.py — Event System

- `HarnessEvent`: dataclass with `type` (str) and `data` (dict)
- `EventSink`: abstract base class with `async emit(event_type, data)`
- `QueueEventSink`: concrete implementation that pushes `HarnessEvent` to `asyncio.Queue`

### audio.py — PCM Utilities

- `pcm_s16le_to_float32()`: int16 PCM → float32 numpy array (for VAD/ASR)
- `float_audio_to_pcm_s16le_bytes()`: float32 numpy → int16 bytes (for TTS output)
- `float_audio_to_wav_bytes()`: float32 numpy → WAV bytes (with header)
- `make_tone_wav()`: generates test tone WAV (for diagnostics)

### audio_frames.py — Frame Handling

- `AudioFrame`: parsed from base64 WebSocket messages — contains PCM bytes, sample rate, channels
- `parse_audio_frame()`: validates frame, extracts PCM data
- `compute_pcm16_level()`: RMS level metering for UI VU meter

### runtime_settings.py — Settings Singleton

See [Runtime Settings Architecture](#runtime-settings-architecture).

### tts_runtime.py — TTS Hot-Swap

See [TTS Runtime Manager](#tts-runtime-manager).

### doctor.py — Diagnostics

Health check system that verifies: Python version, profile validity, required packages, port availability, CUDA/cuDNN presence, Vulkan availability, llama.cpp server reachability.

---

## Provider System

### Protocol Definitions (base.py)

Four provider protocols define the contracts:

```python
class VADProvider(Protocol):
    async def process_frame(self, frame: AudioFrame) -> list[VADEvent]: ...
    async def check_status(self) -> ProviderStatus: ...

class ASRProvider(Protocol):
    async def transcribe_text_input(self, text: str) -> AsyncIterator[TranscriptEvent]: ...
    async def transcribe_audio(self, pcm_s16le, sample_rate, progress=None) -> AsyncIterator[TranscriptEvent]: ...

class LLMProvider(Protocol):
    async def stream_response(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...

class TTSProvider(Protocol):
    async def stream_audio_with_progress(self, text: str, progress=None) -> AsyncIterator[AudioChunk]: ...
    async def load(self) -> ProviderStatus: ...
    async def unload(self) -> ProviderStatus: ...
```

Each provider also reports `ProviderCapabilities` (flags like `supports_partials`, `supports_streaming_tts`, `supports_barge_in`, `requires_gpu`) and `ProviderStatus` (loaded, error, model path).

### Provider Factory (factory.py)

`build_provider_bundle(config)` maps profile config sections to concrete classes:

| Component | Config Key | Builders |
|-----------|-----------|----------|
| VAD | `vad.provider` | `silero` → `SileroVADProvider`, `mock` → `MockVADProvider` |
| ASR | `asr.provider` | `faster-whisper` → `FasterWhisperASRProvider`, `mock` → `MockASRProvider` |
| LLM | `llm.provider` | `llamacpp` → `LlamaCppProvider`, `mock` → `MockLLMProvider` |
| TTS | `tts.provider` | `pocket-tts` → `PocketTTSProvider`, `kokoro-onnx` → `KokoroOnnxProvider`, `mock` → `MockTTSProvider` |

Unrecognized provider names fall back to `UnavailableProvider` (raises on all operations).

### Concrete Providers

#### SileroVADProvider (silero.py)
- **ONNX inference on CPU** — always-on, low-cost VAD
- Windowed processing: 512-sample windows with configurable threshold
- State machine: `speech_start` / `speech_end` with hangover counter
- Lazy model load: `_ensure_model()` loads ONNX on first frame
- Config: `speech_threshold` (default 0.5), `silence_threshold` (default 0.35), `hangover_frames` (default 15)

#### FasterWhisperASRProvider (faster_whisper.py)
- CTranslate2-based Whisper inference (CPU or CUDA)
- `transcribe_audio()`: runs blocking model in `asyncio.to_thread`
- `transcribe_text_input()`: pass-through for typed input (no ASR needed)
- Lazy model load with `model_size` from config (e.g. `large-v3`, `medium`)
- Config: `device` (cpu/cuda), `compute_type` (float16/int8), `language`

#### LlamaCppProvider (llamacpp.py)
- **HTTP client** to llama.cpp server's OpenAI-compatible `/v1/chat/completions`
- Streaming via `httpx` with SSE parsing
- Model auto-resolution: queries `/v1/models` to get actual model ID
- `set_runtime_settings()`: receives `RuntimeSettings` reference
- Every request calls `effective_sampler()` for three-tier merge of sampler params
- Config: `base_url` (default `http://127.0.0.1:8080`), `model` (optional override)

#### PocketTTSProvider (pocket_tts.py)
- **In-process TTS** — runs model in a background thread
- PCM s16le output, streamed as raw chunks
- **PCM coalescing**: accumulates tiny model chunks into `coalesce_ms`-sized packets before sending over WebSocket (avoids per-chunk overhead)
- Voice state management: loads model with selected voice
- Quantization support: fp32, int8

#### KokoroOnnxProvider (kokoro_onnx.py)
- **ONNX TTS** with Misaki G2P phonemization for English
- Model download with cache (Hugging Face hub)
- ONNX session options tuning for CPU performance
- Voice list from `tts-presets.json`
- Lazy load: downloads model on first use if not cached

#### Mock Providers (mock.py)
- Configurable delays for testing
- `MockVADProvider`: emits random probabilities
- `MockASRProvider`: returns canned transcription
- `MockLLMProvider`: streams canned response token by token
- `MockTTSProvider`: generates sine wave audio

#### UnavailableProvider (unavailable.py)
- Fallback stub — all methods raise `ProviderUnavailableError`
- Used when profile references a provider that can't be built

---

## Event System & Real-Time Communication

### WebSocket Protocol

Single endpoint `/ws/events` with JSON messages. Each message has a `type` field and optional `data` object.

#### Client → Server Events

| Event | Description |
|-------|-------------|
| `audio.frame` | Raw PCM audio chunk (base64 `pcm_s16le`, 16kHz) |
| `user.text` | Typed user message |
| `user.continue` | Request to continue last assistant response |
| `vad.speech_start` | Browser-side VAD trigger (for manual barge-in) |

#### Server → Client Events

| Event | Description |
|-------|-------------|
| `vad.probability` | VAD speech probability per frame |
| `vad.speech_start` | Server-side VAD detected speech onset |
| `vad.speech_end` | Server-side VAD detected speech offset |
| `vad.speech_rejected` | Utterance too short (noise gate) |
| `asr.transcript` | Final ASR transcription result |
| `llm.token` | Individual LLM token (streaming) |
| `llm.done` | LLM generation complete |
| `tts.audio` | PCM audio chunk (base64, with metadata: sample_rate, channels, encoding, duration) |
| `tts.cancelled` | TTS playback cancelled (barge-in) |
| `settings.updated` | Runtime settings changed (broadcast to all tabs) |
| `status` | Provider status update |
| `error` | Error notification |

### Event Routing

1. Provider emits event → `QueueEventSink.emit()` → `asyncio.Queue`
2. WS sender coroutine reads queue → serializes to JSON → sends over WebSocket
3. All connected clients receive events (broadcast via `ACTIVE_QUEUES`)

---

## Data Flow Pipeline

### Full Voice Turn

```
1. Browser mic → ScriptProcessor → downsample 48kHz→16kHz → base64 PCM
2. WS send: { type: "audio.frame", data: { pcm: "<base64>", sample_rate: 16000 } }
3. main.py receiver: parse_audio_frame() → SileroVADProvider.process_frame()
4. VAD emits vad.speech_start → main.py starts buffering utterance
5. VAD emits vad.speech_end → main.py applies min_speech_duration_ms gate
   - If too short: emit vad.speech_rejected, drop audio
   - If long enough: proceed to ASR
6. FasterWhisperASRProvider.transcribe_audio(pcm) → asyncio.to_thread
   - Emits asr.transcript (final text)
7. ConversationOrchestrator.handle_audio_turn():
   - Append user message to history
   - Build messages with effective_system_prompt()
   - LlamaCppProvider.stream_response(messages)
8. Orchestrator _stream_llm_and_tts():
   - Buffer tokens in sentence_buffer
   - should_flush_tts(): flush on sentence boundary OR char limit
   - Start TTS chunk: PocketTTS/KokoroOnnx.stream_audio_with_progress()
9. TTS generates pcm_s16le chunks → base64 → tts.audio event
10. Browser decodePcm16Chunk() → AudioBuffer → Web Audio start(startAt)
11. Queue-based gapless playback with nextAudioTime tracking
```

### Text Turn (simplified)

```
user.text → handle_text_turn() → LLM stream → chunked TTS → audio playback
```

### Continue Turn

```
user.continue → handle_continue() → keep last assistant as prefix → LLM continue → new tokens → TTS only new tokens
```

---

## Barge-In Flow

When the user speaks while TTS is playing:

```
1. VAD detects speech_start OR user clicks "Barge In" button
2. main.py: orchestrator.cancel_tts("vad_barge_in")
3. cancel_tts():
   - Cancels all active TTS asyncio tasks
   - Awaits their cancellation (prevents stale TTS from racing)
   - Emits tts.cancelled
4. Browser receives tts.cancelled → stopAudio()
   - Stops all scheduled Web Audio sources
   - Resets playback queue
5. New utterance processing begins normally
```

**Key safety**: `cancel_tts` awaits all cancelled tasks before returning, so stale LLM/TTS streams cannot race into the next user turn.

---

## Character Card System

Supports TavernAI V2 character cards for persona/behavior customization.

### Upload Flow

1. User drops PNG or JSON file on the character card drop zone
2. Frontend sends to `/api/settings/character/upload`
3. Backend routes:
   - PNG: `parse_character_png()` → scan PNG chunks for `tEXt` with key `chara` → base64 decode → JSON
   - JSON: `parse_character_json()` → direct parse
4. Validate and size-limit (prevents unbounded base64 decode)
5. `RUNTIME_SETTINGS.set_character(card)` → `save_runtime_settings()` → broadcast `settings.updated`
6. `CHARACTER_SEED_HOOKS` trigger `orchestrator.seed_character_first_message()`
7. If conversation empty: insert `first_mes` (with `{{user}}`/`{{char}}` substitution) as assistant message
8. TTS does **not** auto-play the seed message

### Prompt Assembly

`effective_system_prompt()` layers:

```
1. Character card prompt (description + personality + scenario + mes_example)
   OR name header (if no card: "You are <ai_name>. <user_name> is talking to you.")
2. + additional_system_prompt (from settings)
3. + manual_prompt (from chat textarea)
```

---

## Runtime Settings Architecture

`RuntimeSettings` is a singleton (`RUNTIME_SETTINGS` in main.py) that holds all runtime-adjustable configuration.

### Three-Tier Sampler Merge

```
effective_sampler() returns merged dict:
  1. server_defaults    ← from llama.cpp /props endpoint
  2. profile_defaults   ← from active profile JSON (llm section)
  3. llm_overrides      ← from user UI adjustments

Each tier overrides the previous: server < profile < user
```

### Persistence

- Saved to `user_settings.json` in project root
- Survives server restarts
- Loaded at startup via `load_runtime_settings()`

### Key Fields

| Field | Purpose |
|-------|---------|
| `llm_overrides` | User sampler overrides (temperature, top_p, etc.) |
| `user_name` / `ai_name` | Conversation participant names |
| `character` | Active CharacterCard (or None) |
| `additional_system_prompt` | Extra instructions appended to system prompt |

### Broadcast

Settings changes broadcast `settings.updated` to all connected WebSocket clients, keeping multiple browser tabs in sync.

---

## TTS Runtime Manager

`TTSRuntimeManager` manages TTS provider hot-swapping at runtime.

### TTSPreset System

Defined in `profiles/tts-presets.json`:

| Preset | Provider | Voices |
|--------|----------|--------|
| `pocket-tts-fp32` | Pocket TTS (fp32) | Default |
| `pocket-tts-int8` | Pocket TTS (int8 quantized) | Default |
| `kokoro-v1.0` | Kokoro ONNX | Multiple (af_bella, af_nicole, etc.) |

### Hot-Swap Flow

```
1. User selects new TTS preset/voice in Settings UI
2. Frontend sends /api/tts/preset or /api/tts/voice
3. TTS_RUNTIME_MANAGER.change_preset() or .change_voice():
   - unload() current TTS provider
   - build new TTS provider via factory
   - load() new provider
   - Update ProviderBundle.tts reference
4. TURN_CONFIG_HOOKS → orchestrator.update_tts_provider()
   - Next turn uses new TTS provider
   - Active turns keep old provider reference (safe)
```

### API Endpoints

| Endpoint | Purpose |
|----------|---------|
| `GET /api/tts/presets` | List available TTS presets |
| `POST /api/tts/preset` | Switch TTS preset |
| `POST /api/tts/voice` | Switch voice within preset |
| `GET /api/tts/status` | Current TTS provider status |
| `POST /api/tts/reload` | Reload current TTS model |

---

## Frontend Architecture

Single-page application in `app/static/`. No build step, no framework.

### index.html — App Shell

Two main views:
- **Chat view**: message bubbles, mic button, text input, continue button, barge-in button
- **Settings view**: sampler sliders, character card drop zone, TTS preset/voice selectors, provider status cards

### app.js — Client Logic (~1086 lines)

Key subsystems:

| Subsystem | Implementation |
|-----------|---------------|
| **WebSocket** | Auto-reconnect, event dispatch by type |
| **Mic capture** | `ScriptProcessorNode` → downsample to 16kHz → base64 → WS `audio.frame` |
| **Audio playback** | Web Audio API: PCM → AudioBuffer → scheduled `start(startAt)`, queue-based gapless |
| **VU meter** | `compute_pcm16_level` for level display |
| **Settings sync** | Two-way: UI ↔ REST + WebSocket `settings.updated` broadcast |
| **Character upload** | Drag-and-drop + fetch to `/api/settings/character/upload` |
| **Barge-in** | Button sends `vad.speech_start` + calls `stopAudio()` |

#### Audio Playback Detail

The browser does **not** use per-chunk `new Audio()`. Instead:

1. `decodePcm16Chunk()`: builds `AudioBuffer` directly from raw PCM s16le
2. `playAudioQueue()`: schedules via `source.start(nextAudioTime)` with lead time tracking
3. `stopAudio()`: stops all scheduled sources, clears queue

This avoids the choppy playback that results from creating many tiny WAV chunks.

#### Mic Resampling

Browser captures at device rate (typically 48kHz). Simple averaging downsample converts to 16kHz:

```javascript
// Simple linear interpolation downsample
const ratio = deviceSampleRate / 16000;
for (let i = 0; i < targetLength; i++) {
    output[i] = input[Math.floor(i * ratio)];
}
```

### styles.css — Theming

- CSS custom properties for light/dark themes
- Responsive grid layout with breakpoints
- VU meter animation, message bubble styling, provider status cards

---

## Profile & Configuration System

### Profile JSON Structure

Each profile in `profiles/` defines all four provider configs:

```json
{
  "vad": {
    "provider": "silero",
    "speech_threshold": 0.6,
    "silence_threshold": 0.35,
    "hangover_frames": 15,
    "min_speech_duration_ms": 200
  },
  "asr": {
    "provider": "faster-whisper",
    "model_size": "large-v3",
    "device": "cuda",
    "compute_type": "float16",
    "language": "en"
  },
  "llm": {
    "provider": "llamacpp",
    "base_url": "http://127.0.0.1:8080",
    "temperature": 0.7,
    "top_p": 0.9
  },
  "tts": {
    "provider": "pocket-tts",
    "preset": "pocket-tts-fp32",
    "voice": "default"
  }
}
```

### Available Profiles

| Profile | VAD | ASR | LLM | TTS | Notes |
|---------|-----|-----|-----|-----|-------|
| `llamacpp-cuda-asr` | Silero | Faster-Whisper (CUDA) | llama.cpp | Pocket TTS | Default, best quality |
| `llamacpp-local` | Silero | Faster-Whisper (CPU) | llama.cpp | Pocket TTS | CPU-only fallback |
| `llamacpp-kokoro-onnx` | Silero | Faster-Whisper (CPU) | llama.cpp | Kokoro ONNX | Alternative TTS |
| `mock-local` | Mock | Mock | Mock | Mock | Testing/development |

---

## Startup & Operational Scripts

### install.ps1 / install.sh

- Detects Python 3.11–3.13
- Creates `.venv` if missing
- `pip install -r requirements.txt`
- Windows: adds `nvidia-cublas-cu12` for CTranslate2 CUDA support

### start.ps1 / start.sh

- Activates `.venv`
- Adds NVIDIA bin directories to `PATH` (Windows: ensures `cublas64_12.dll` is findable)
- Launches `uvicorn conversational_harness.main:app --host 0.0.0.0 --port 7860`
- Accepts `--profile` argument (defaults to `llamacpp-cuda-asr`)

### stop.ps1 / stop.sh

- Finds process owning port 7860
- Sends graceful shutdown

### doctor.ps1 / doctor.sh / doctor.py

Pre-flight diagnostic checks:
1. Python version (3.11–3.13)
2. Profile file exists and is valid JSON
3. Required pip packages installed
4. Port 7860 available
5. CUDA/cuDNN libraries findable
6. Vulkan availability (experimental)
7. llama.cpp server reachable at configured `base_url`

### update.ps1 / update.sh

- `git pull`
- `pip install -r requirements.txt` (update dependencies)

---

## Test Suite

Tests use `pytest` with `pythonpath=app` and `testpaths=tests`.

| Test File | Coverage |
|-----------|----------|
| `test_orchestrator.py` | Event emission, system prompt assembly, character seeding, conversation clear |
| `test_config.py` | Profile loading, provider building, TTS preset matching |
| `test_silero_vad.py` | VAD state machine (fake model/torch), threshold behavior |
| `test_tts_runtime.py` | TTS preset switching, voice selection, load/unload lifecycle |
| `test_audio_frames.py` | Frame parsing, sample rate validation, level metering |
| `test_audio.py` | WAV tone header validation |
| `test_faster_whisper.py` | Fake WhisperModel, transcription, sample rate rejection |
| `test_pocket_tts.py` | Fake Pocket model, PCM streaming, quantize forwarding |
| `test_kokoro_onnx.py` | Fake Kokoro model, G2P phonemization, error handling |
| `test_llamacpp_live.py` | Opt-in live llama.cpp streaming test (requires running server) |
| `test_doctor.py` | Monkeypatched health checks, port owner detection |
| `test_character_settings_api.py` | FastAPI TestClient: JSON/PNG character upload, size limits, validation |

Testing patterns:
- Fake/stub models to avoid loading real AI models in CI
- `asyncio.to_thread` mocked for ASR tests
- Live integration test (`test_llamacpp_live.py`) is opt-in only

---

## Key Design Patterns

| Pattern | Where | Purpose |
|---------|-------|---------|
| **Singleton globals** | `main.py` | `BASE_CONFIG`, `TTS_MANAGER`, `RUNTIME_SETTINGS`, `ACTIVE_QUEUES` — module-level instances shared across all WS connections |
| **Provider factory** | `factory.py` | Maps config sections to Protocol-implementing classes; `TTS_PROVIDER_BUILDERS` dict for TTS extensibility |
| **Protocol-based interfaces** | `base.py` | VAD/ASR/LLM/TTS defined as Python Protocols — duck typing, swappable implementations |
| **Event-driven architecture** | `events.py` + `orchestrator.py` | All stages emit typed events through `EventSink` → `asyncio.Queue` → WebSocket JSON |
| **Hook sets** | `main.py` | `TTS_CANCEL_HOOKS`, `TURN_CONFIG_HOOKS`, `CHARACTER_SEED_HOOKS` — per-WS orchestrators react to global state |
| **Lazy model loading** | All providers | `_ensure_model()` loads ONNX/CTranslate2/model on first use |
| **TTS hot-swap** | `tts_runtime.py` | `TTSRuntimeManager` rebuilds TTS provider on preset/voice change; orchestrator gets new reference via hooks |
| **Three-tier sampler merge** | `runtime_settings.py` | Server defaults → profile defaults → UI overrides — each tier overrides previous |
| **Layered system prompt** | `runtime_settings.py` | Character card → additional instructions → manual textarea — each layer can be empty |
| **PCM coalescing** | `pocket_tts.py` | Accumulate tiny model chunks into `coalesce_ms`-sized packets before sending over WS |
| **Web Audio scheduled playback** | `app.js` | `source.start(startAt)` with `nextAudioTime` tracking for gapless playback — avoids per-chunk `Audio()` elements |
| **Min speech duration gate** | `main.py` | Rejects utterances shorter than `min_speech_duration_ms` as noise — more robust than threshold tuning alone |
| **Pre-speech ring buffer** | `main.py` | Keeps `pre_speech_frames` audio before `vad.speech_start` to avoid cutting first syllable |
| **Awaited task cancellation** | `orchestrator.py` | `cancel_tts()` awaits all cancelled TTS tasks — prevents stale streams from racing into next turn |
| **PNG chunk scanner** | `runtime_settings.py` | Hand-written PNG tEXt chunk parser — no Pillow dependency — for TavernAI character cards |
| **Template substitution** | `runtime_settings.py` | `{{user}}`/`{{char}}` replacement in character card `mes_example` and `first_mes` fields |

---

## Dependencies

From `requirements.txt`:

| Package | Purpose |
|---------|---------|
| `fastapi` + `uvicorn` | Web framework + ASGI server |
| `httpx` | Async HTTP client (llama.cpp communication) |
| `faster-whisper` | CTranslate2-based ASR |
| `silero-vad` | Voice activity detection |
| `onnxruntime` | ONNX inference runtime (VAD + Kokoro TTS) |
| `pocket-tts` | Local CPU TTS |
| `kokoro-onnx` | Kokoro ONNX TTS |
| `misaki` | G2P phonemization (for Kokoro English) |
| `numpy` | Audio array processing |
| `nvidia-cublas-cu12` | CUDA cuBLAS for CTranslate2 (Windows) |
| `spacy` | NLP pipeline (used by Misaki G2P) |
