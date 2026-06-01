# System Architecture

## Overview

Converse-AI is a local, modular realtime voice+text conversation system. It connects four swappable AI components — Voice Activity Detection (VAD), Automatic Speech Recognition (ASR), Large Language Model (LLM), and Text-to-Speech (TTS) — behind provider interfaces, orchestrated by a pipeline that turns microphone audio into spoken AI responses in real time.

The server is a FastAPI application serving a browser-based UI over HTTP/WebSocket. All inference runs locally (CPU or CUDA). No cloud APIs are required.

```
Browser UI  ←→  FastAPI + WebSocket  ←→  Orchestrator  ←→  Providers (VAD, ASR, LLM, TTS)
```

## Directory Layout

```
Conversational-AI-Harness/
├── app/
│   ├── conversational_harness/        # Python backend package
│   │   ├── main.py                    # FastAPI app, routes, WebSocket handler
│   │   ├── orchestrator.py            # Turn pipeline: ASR → LLM → TTS
│   │   ├── config.py                  # Profile loading, HarnessConfig
│   │   ├── events.py                  # HarnessEvent, EventSink base class
│   │   ├── audio.py                   # PCM conversion utilities
│   │   ├── audio_frames.py            # AudioFrame parsing, level metering, silence trimming
│   │   ├── runtime_settings.py        # RuntimeSettings, CharacterCard, MemoryStore
│   │   ├── tts_runtime.py             # TTSRuntimeManager (preset/voice switching)
│   │   ├── doctor.py                  # Diagnostics CLI
│   │   ├── launch.py                  # Guided startup launcher
│   │   └── providers/
│   │       ├── base.py                # Protocol classes, data types, capabilities
│   │       ├── factory.py             # ProviderBundle construction, builder functions
│   │       ├── silero.py              # Silero VAD (ONNX)
│   │       ├── faster_whisper.py      # faster-whisper ASR (CTranslate2)
│   │       ├── llamacpp.py            # llama.cpp LLM (OpenAI-compatible HTTP)
│   │       ├── pocket_tts.py          # Pocket TTS voice synthesis
│   │       ├── kokoro_onnx.py         # Kokoro v1.0 ONNX TTS
│   │       ├── mock.py                # Mock providers for testing
│   │       └── unavailable.py         # Placeholder for unknown provider names
│   └── static/                        # Browser UI
│       ├── index.html                 # SPA shell: Chat, Companion, Settings tabs
│       ├── app.js                     # WebSocket client, audio capture, playback
│       └── styles.css                 # Layout and theming
├── profiles/                          # JSON configuration profiles
│   ├── llamacpp-cuda-asr.json         # Default: CUDA ASR + Silero + llama.cpp + Pocket TTS
│   ├── llamacpp-local.json            # CPU ASR variant
│   ├── llamacpp-kokoro-onnx.json      # Kokoro ONNX TTS variant
│   ├── mock-local.json                # No-model smoke test
│   └── tts-presets.json               # TTS preset definitions (voices, models, turn config)
├── tests/                             # pytest suite
├── docs/                              # Documentation
├── user_settings.json                 # Persisted runtime settings (sampler overrides, names, cards)
├── start.ps1 / start.sh              # Launch guided startup flow
├── doctor.ps1 / doctor.sh            # Run diagnostics
└── install.ps1 / install.sh          # Create venv + install deps
```

## Core Components

### 1. Configuration System (`config.py`)

`HarnessConfig` wraps a single JSON profile file. Each profile has named sections (`audio`, `vad`, `asr`, `llm`, `tts`, `turn`) that provide key-value configuration to the corresponding provider.

- Profile selection: `HARNESS_PROFILE` env var, CLI argument, or default (`profiles/llamacpp-cuda-asr.json`).
- `HarnessConfig.section(name)` returns a plain dict for that section.
- Profiles are read-once at startup. Runtime mutations go through `RuntimeSettings` or `TTSRuntimeManager`.

### 2. Provider Layer (`providers/`)

#### Provider Protocols (`base.py`)

Four Protocol classes define the provider contracts:

| Protocol | Key Methods | Key Types |
|---|---|---|
| `VADProvider` | `process_frame(frame) → list[VADEvent]`, `check_status()`, `unload()` | `VADEvent` (type, probability, audio_ms) |
| `ASRProvider` | `transcribe_audio(pcm, sr, progress) → AsyncIterator[TranscriptEvent]`, `transcribe_text_input(text)`, `load()`, `unload()` | `TranscriptEvent` (text, final) |
| `LLMProvider` | `stream_response(messages) → AsyncIterator[str]`, `check_status()` | — |
| `TTSProvider` | `stream_audio(text) → AsyncIterator[AudioChunk]`, `stream_audio_with_progress(text, progress)`, `load()`, `unload()` | `AudioChunk` (data, sample_rate, encoding, duration_ms, final) |

All providers report `ProviderStatus` (name, kind, ready, message, capabilities).

#### Provider Factory (`factory.py`)

`build_provider_bundle(config)` constructs a `ProviderBundle` (vad + asr + llm + tts) by reading the provider name from each config section and dispatching to the appropriate builder. Unknown provider names yield `UnavailableProvider`.

#### Concrete Providers

| Provider | Class | Backend | Notes |
|---|---|---|---|
| **Silero VAD** | `SileroVADProvider` | `silero-vad` + ONNX on CPU | Configurable threshold, hysteresis (neg_threshold), hangover, window size. State machine emits `vad.speech_start` / `vad.speech_end`. |
| **faster-whisper** | `FasterWhisperASRProvider` | `faster-whisper` (CTranslate2) | CUDA or CPU. Lazy model load on first transcription. Supports beam search, VAD filter, hallucination suppression parameters. |
| **llama.cpp** | `LlamaCppProvider` | External llama.cpp server via HTTP | OpenAI-compatible `/v1/chat/completions` streaming. Auto-resolves model from `/v1/models`. Sampler params merged at runtime from server defaults → profile → user overrides. |
| **Pocket TTS** | `PocketTTSProvider` | `pocket-tts` in-process | Streaming PCM output with configurable coalescing window. Runs synthesis in a background thread. |
| **Kokoro ONNX** | `KokoroOnnxProvider` | `kokoro-onnx` + ONNX Runtime (CPU) | Auto-downloads model + voices from GitHub. Optional Misaki G2P phonemization for English. Streaming with generation lock. |
| **Mock** | `MockVADProvider`, `MockASRProvider`, `MockLLMProvider`, `MockTTSProvider` | No external deps | Deterministic responses with configurable latency delays. Used by `mock-local` profile for smoke tests. |

### 3. Audio Processing (`audio.py`, `audio_frames.py`)

- **`AudioFrame`**: parsed from WebSocket `audio.frame` messages. Carries base64-decoded PCM s16le data with sequence number, sample rate, channels, frame duration, and encoding.
- **`parse_audio_frame()`**: validates frame parameters against expected config (sample rate, channels, frame_ms, encoding).
- **`AudioFrameStats`**: tracks received/dropped frames, emits periodic `audio.input_level` metrics (RMS, peak).
- **`compute_pcm16_level()`**: RMS and peak calculation for PCM s16le buffers.
- **`trim_pcm16_silence()`**: strips leading/trailing silence frames below an RMS threshold.
- **`pcm_s16le_to_float32()`**, **`float_audio_to_pcm_s16le_bytes()`**: format conversion utilities shared by ASR and TTS providers.

### 4. Conversation Orchestrator (`orchestrator.py`)

`ConversationOrchestrator` drives the cascade pipeline for each user turn:

```
User input (text or audio) → ASR (if audio) → LLM stream → sentence chunk → TTS stream → audio output
```

Key responsibilities:
- **Dual-mode state**: Maintains separate `TurnState` for `chat` and `companion` modes, each with its own message history and system prompt.
- **Turn lifecycle**: `handle_text_turn()`, `handle_audio_turn()`, `handle_continue()`. Each emits structured events (`turn.started`, `asr.transcript`, `llm.token`, `tts.audio`, `turn.finished`).
- **Streaming TTS chunking**: Accumulates LLM tokens into sentences, then dispatches each sentence to TTS as a concurrent `asyncio.Task`. Configurable `tts_chunk_chars` and `min_tts_chars` control chunk boundaries.
- **Barge-in / cancellation**: `cancel_tts()` cancels all active TTS tasks and awaits their completion to prevent stale streams from racing into the next turn.
- **Conversation history**: Maintains `messages` list (role/content dicts) per mode. Feeds full history to LLM for context.
- **System prompt assembly**: Delegates to `RuntimeSettings.effective_system_prompt()`.
- **Character card seeding**: `seed_character_first_message()` injects a TavernAI character's `first_mes` as the initial assistant message in empty conversations.

### 5. Runtime Settings (`runtime_settings.py`)

`RuntimeSettings` is a singleton holding all runtime-adjustable state:

- **Sampler overrides**: Three-tier merge for LLM parameters (temperature, top_k, top_p, etc.): llama.cpp server defaults → profile JSON defaults → user UI overrides. `effective_sampler()` returns the merged dict. Updated live without server restart.
- **System prompt layers**: `effective_system_prompt()` assembles: character card (or name header) → additional instructions → manual textarea. Companion mode has its own prompt path with memory injection.
- **Character cards**: Supports TavernAI V2 format (PNG with base64 `chara` tEXt chunk, or standalone JSON). `{{user}}` / `{{char}}` template substitution applied.
- **Companion settings**: Separate user/AI names, system prompt, sampler overrides, and `memory_enabled` flag.
- **Memory**: Companion mode can read/write `memory.md` (capped at 20,000 chars) as long-term context injected into the system prompt.
- **Persistence**: Saved to `user_settings.json` on every change. Survives server restarts.

### 6. TTS Runtime Manager (`tts_runtime.py`)

`TTSRuntimeManager` handles dynamic TTS preset and voice switching at runtime:

- Loads presets from `profiles/tts-presets.json`.
- Default preset matched from the active profile's TTS section.
- `select_preset(id)` / `select_voice(id)` rebuild the TTS provider in-place.
- `load_selected()` / `unload_selected()` control model lifecycle (useful for VRAM management).
- `describe()` returns the full runtime state (available presets, voices, current selection) for the UI.

### 7. FastAPI Application (`main.py`)

#### HTTP Routes

| Route | Purpose |
|---|---|
| `GET /` | Serves `index.html` |
| `GET /api/status` | Provider statuses, profile info, TTS runtime state |
| `POST /api/settings` | Update runtime settings (sampler overrides, names, system prompt) |
| `POST /api/character` | Upload character card (JSON or PNG) |
| `DELETE /api/character` | Clear active character card |
| `GET /api/settings/export` | Export current settings as JSON |
| `POST /api/tts/select` | Switch TTS preset |
| `POST /api/tts/load` | Load selected TTS model |
| `POST /api/tts/unload` | Unload selected TTS model |
| `POST /api/tts/voice` | Switch TTS voice within preset |
| `GET /api/profile` | Raw profile JSON |
| `GET /api/memory` | Companion memory content |
| `POST /api/memory` | Update companion memory |

#### WebSocket Endpoint: `/ws/events`

The primary realtime channel. Bidirectional JSON messages.

**Client → Server messages:**

| Type | Description |
|---|---|
| `user.text` | Text utterance for processing |
| `user.continue` | Continue last assistant response |
| `audio.frame` | PCM audio frame (base64 `pcm_s16le`) |
| `vad.speech_start` | Browser-side speech detection (mock VAD mode) |
| `vad.speech_end` | Browser-side speech end |
| `system_prompt.update` | Update system prompt |
| `conversation.clear` | Clear conversation history |
| `tts.cancel` | Cancel active TTS |

**Server → Client messages (event stream):**

| Type | Description |
|---|---|
| `profile.loaded` | Active profile name |
| `providers.status` | All provider statuses + TTS runtime info |
| `audio.input_level` | Periodic RMS/peak metering |
| `vad.speech_start` / `vad.speech_end` | VAD state transitions |
| `vad.speech_rejected` | Utterance below min duration |
| `asr.started` / `asr.transcript` / `asr.progress` / `asr.error` | ASR lifecycle |
| `llm.token` / `llm.done` | Streaming LLM tokens and completion |
| `tts.audio` / `tts.progress` / `tts.cancelled` | TTS audio chunks and lifecycle |
| `turn.started` / `turn.finished` / `turn.cancelled` | Turn lifecycle |
| `conversation.cleared` / `conversation.seeded` | Conversation management |
| `settings.updated` | Runtime settings change broadcast |
| `system_prompt.updated` | System prompt change confirmation |

#### Audio Pipeline in WebSocket Handler

The `/ws/events` handler contains the VAD-driven audio pipeline:

1. **Frame ingestion**: Browser sends `audio.frame` messages (30ms PCM s16le chunks).
2. **Pre-buffering**: Maintains a rolling buffer of recent frames (configurable `pre_speech_ms`, default 450ms) to capture speech onset.
3. **VAD processing**: Each frame goes to the VAD provider. Silero runs a state machine with speech threshold + hysteresis + hangover.
4. **Utterance collection**: On `vad.speech_start`, pre-buffer is dumped into the utterance buffer and recording begins. On `vad.speech_end`, the full utterance is assembled.
5. **Noise rejection**: Multi-layer defense:
   - `min_speech_duration_ms` gate (default 300ms) rejects short transients.
   - `reject_low_energy_rms` rejects low-energy utterances up to `reject_low_energy_max_duration_ms`.
   - `reject_utterance_rms` sets a floor for all utterances.
   - `trim_silence_rms` / `trim_silence_frame_ms` trims edge silence before ASR.
6. **Barge-in**: When VAD detects speech start while TTS is playing, the orchestrator cancels active TTS tasks before collecting the new utterance.
7. **ASR turn**: The utterance PCM is sent to the ASR provider, then the orchestrator runs the LLM → TTS pipeline.

### 8. Event System (`events.py`)

Minimal event abstraction:
- `HarnessEvent`: typed event with payload dict and timestamp.
- `EventSink`: abstract base with `emit(event_type, **payload)`. Concrete implementation in `main.py` wraps an `asyncio.Queue` that feeds the WebSocket sender loop.

### 9. Browser UI (`app/static/`)

Single-page application with three tabs:

- **Chat**: Conversation transcript, text input, continue button, latency metrics sidebar, event log sidebar.
- **Companion**: Separate conversation context with its own prompt, memory, and settings.
- **Settings**: Sampler overrides, user/AI names, system prompt, character card upload, TTS preset/voice selection, TTS load/unload.

Audio features:
- Microphone capture via `getUserMedia`, framed into 30ms PCM s16le chunks.
- Audio playback via Web Audio API, building `AudioBuffer` directly from PCM (avoids per-chunk WAV decode overhead).
- Input level metering and VAD state display.
- Browser-side speech detection as fallback when server-side VAD is unavailable (mock VAD mode).

### 10. Startup Launcher (`launch.py`)

Shared startup coordinator used by `start.ps1` and `start.sh`.

- Resolves `HARNESS_PROFILE` / `--profile` and `HARNESS_PORT` / `--port`.
- Lists valid harness profiles with `--list-profiles` (filters out non-profile JSON such as TTS presets).
- Runs doctor-style preflight checks before starting the app.
- Starts uvicorn as a foreground subprocess.
- Waits for `/api/status` before reporting the server ready.
- Prompts before opening the browser in interactive terminals.
- Supports non-interactive startup with `--no-prompt --no-browser`, which keeps the path Docker-friendly.

The launcher checks external dependencies such as llama.cpp but does not start or supervise them.

### 11. Diagnostics (`doctor.py`)

CLI tool (`doctor.ps1` / `doctor.sh`) that checks:
- Python version
- Profile validity
- Required packages (fastapi, uvicorn)
- Port 7860 availability
- CUDA / Vulkan tooling
- llama.cpp server reachability
- Each provider's status (VAD, ASR, LLM, TTS)

Outputs structured `[OK]` / `[WARN]` results. Exits non-zero on critical failures.

## Data Flow: Voice Turn

```
┌──────────┐    audio.frame     ┌──────────────┐
│  Browser │ ────────────────── │  FastAPI WS   │
│  Mic     │    (base64 PCM)    │  /ws/events   │
└──────────┘                    └──────┬───────┘
                                       │
                          ┌────────────▼────────────┐
                          │  VAD (Silero / Mock)    │
                          │  per-frame probability   │
                          │  state machine:          │
                          │  idle → speaking → idle  │
                          └────────────┬────────────┘
                                       │ vad.speech_end
                          ┌────────────▼────────────┐
                          │  Noise Rejection         │
                          │  • min_speech_duration   │
                          │  • low energy RMS gate   │
                          │  • silence edge trimming  │
                          └────────────┬────────────┘
                                       │ clean PCM
                          ┌────────────▼────────────┐
                          │  ASR (faster-whisper)    │
                          │  transcribe_audio()      │
                          │  → TranscriptEvent       │
                          └────────────┬────────────┘
                                       │ final transcript
                          ┌────────────▼────────────┐
                          │  LLM (llama.cpp)         │
                          │  stream_response()       │
                          │  → token stream          │
                          └────────────┬────────────┘
                                       │ sentence chunks
                          ┌────────────▼────────────┐
                          │  TTS (Pocket / Kokoro)   │
                          │  stream_audio()          │
                          │  → AudioChunk stream     │
                          └────────────┬────────────┘
                                       │ tts.audio events
┌──────────┐    PCM via WebSocket     ┌▼─────────────┐
│  Browser │ ◄─────────────────────── │  FastAPI WS   │
│  Speaker │    (base64 PCM)          │               │
└──────────┘                          └──────────────┘
```

## Data Flow: Text Turn

Same as voice turn, but skips VAD and ASR. The text input goes directly to the orchestrator's `handle_text_turn()`, which feeds the LLM and streams the response through TTS.

## Modes

### Chat Mode

Default mode. Single conversation thread with optional character card. System prompt assembled from character card (or name header) + additional instructions + manual textarea.

### Companion Mode

Separate conversation state with:
- Independent user/AI names and system prompt.
- Independent sampler overrides.
- Optional long-term memory (`memory.md`) injected into the system prompt.
- Memory is loaded/saved via `/api/memory` endpoints and managed by `MemoryStore`.

## Startup Sequence

1. `start.ps1` / `start.sh` verifies `.venv`, sets `PYTHONPATH`, adds CUDA package bin paths to `PATH` (Windows), and delegates to `python -m conversational_harness.launch`.
2. `launch.py`:
   - Resolves profile and port from CLI args, environment variables, or defaults.
   - Optionally prompts for profile selection in interactive terminals.
   - Runs preflight checks for Python/package readiness, profile validity, port availability, provider status, optional GPU tooling, and llama.cpp readiness.
   - Starts uvicorn as a foreground subprocess.
   - Polls `/api/status` until the FastAPI app is ready, then prints the URL and optionally opens the browser.
3. `main.py` `lifespan()` handler:
   - Loads the active profile via `load_config()`.
   - Creates `TTSRuntimeManager` from profile + TTS presets.
   - Creates `RuntimeSettings` singleton.
   - Fetches llama.cpp server defaults from `/props` endpoint.
4. On WebSocket connect:
   - Builds a fresh `ProviderBundle` from the profile.
   - Injects `RuntimeSettings` into the LLM provider.
   - Creates an `asyncio.Queue`-backed `EventSink`.
   - Instantiates `ConversationOrchestrator` with the bundle.
   - Warms up ASR model (lazy load triggered).
   - Seeds character card first message if applicable.
   - Starts sender/receiver coroutines.

## Key Design Decisions

1. **Provider protocols, not classes**: Each provider implements a Python `Protocol` (structural typing). No inheritance required. Swapping a provider means changing one JSON field in the profile.

2. **Profile-driven configuration**: All provider settings come from JSON profiles. No hardcoded model paths or sampler values in orchestration code.

3. **Lazy model loading**: ASR and TTS models load on first use (or on WebSocket connect for ASR). This keeps startup fast and avoids loading models that won't be used.

4. **Streaming everywhere**: ASR streams partials, LLM streams tokens, TTS streams audio chunks. The orchestrator pipelines these so TTS starts before the full LLM response is complete.

5. **Cancellation-first design**: Active turns are `asyncio.Task` objects that are properly cancelled and awaited before starting new turns. This prevents stale streams from racing.

6. **Three-tier sampler merge**: Server defaults → profile → user overrides. The UI shows effective values; changes take effect immediately without restart.

7. **Layered system prompt**: Character card, additional instructions, and manual textarea are assembled at request time. Each layer can be empty.

8. **PCM-native audio transport**: Raw `pcm_s16le` over WebSocket, not WAV. The browser builds `AudioBuffer` directly from PCM frames, avoiding per-chunk decode overhead.

9. **Shared startup logic**: Platform scripts stay thin. Startup policy lives in `launch.py`, so human-guided startup and future non-interactive/container entrypoints use the same profile, check, and readiness path.

## Testing

Tests in `tests/` cover:
- Audio utilities (PCM conversion, framing, level metering, silence trimming)
- Configuration loading and profile selection
- Provider status checks (VAD, ASR, LLM, TTS)
- Orchestrator turn handling
- Doctor diagnostics
- Guided launcher behavior
- TTS runtime (preset/voice switching)
- Character card parsing (JSON and PNG)
- Live integration tests for llama.cpp and faster-whisper

The `mock-local` profile enables full-pipeline testing without any AI models installed.
