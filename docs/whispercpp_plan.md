# Plan: Whisper.cpp Server as Optional ASR Provider

## Goal

Add whisper.cpp server (`whisper-server`) as a non-CUDA ASR backend option while keeping faster-whisper as the default. Users without NVIDIA GPUs (AMD, Intel, Apple Silicon) can use Vulkan, Metal, or CPU via whisper.cpp.

---

## Tasks

### 1. Create `WhisperCppServerASRProvider` class

**File:** `app/conversational_harness/providers/whisper_cpp.py` (new)

Implement `ASRProvider` protocol against whisper.cpp's HTTP server API.

**Config keys** (read from profile `"asr"` section):

| Key | Default | Purpose |
|---|---|---|
| `provider` | `"whisper-cpp"` | Factory selector |
| `base_url` | `"http://127.0.0.1:8082"` | whisper-server address |
| `model` | `"ggml-small.en.bin"` | GGML model file path |
| `language` | `"en"` | Language code |
| `temperature` | `0` | Sampling temp (0 = greedy) |
| `timeout_s` | `120` | HTTP request timeout |
| `reject_low_energy_rms` | `0.003` | Existing noise gate — stays in main.py |
| `reject_utterance_rms` | `0.002` | Existing noise gate — stays in main.py |
| `trim_silence_rms` | `0.003` | Existing noise gate — stays in main.py |

**Private subprocess management** (modeled on how existing `llamacpp.py` manages llama-server):

- `ensure_server()` — start `whisper-server` subprocess if not running. Check via HTTP GET `/health` or `/v1/models`.
- Command line: `whisper-server --model <path> --port 8082 --no-timestamps` (plus optional `--gpu` backend flag).
- `stop_server()` — terminate subprocess on `unload()`.
- Constructor accepts optional pre-existing subprocess handle for external lifecycle management (power users).

**Transcription flow (`transcribe_audio`):**

1. Convert PCM s16le to WAV in-memory (using existing `audio.py` utilities or `wave` module).
2. POST WAV bytes to `http://{base_url}/inference` with `Content-Type: audio/wav`.
3. Parse response JSON — extract `"text"` field.
4. Yield single `TranscriptEvent(text=transcript, final=True)`.

**No partials in initial version.** whisper.cpp server supports streaming via chunked POST, but start with full-utterance transcription. Streaming can be added later.

**Text input (`transcribe_text_input`):** passthrough, identical to faster-whisper provider — yield `TranscriptEvent(text, final=True)`.

**Subprocess lifecycle:**

- `load()` — calls `ensure_server()`. Starts server if not running, polls `/health` until ready (max 30s).
- `unload()` — calls `stop_server()`. Sends SIGTERM, waits 5s, SIGKILL if still alive.
- `check_status()` — GET `/health`. Returns `ProviderStatus(ready=True)` on 200, `ready=False` with error message on failure.
- Property `status` — returns current known state.

**Error handling:**

- Connection refused → server not started yet or crashed → try restart in `ensure_server()`.
- Timeout → wrap POST in `asyncio.wait_for(...)`.
- Empty/error response → raise `RuntimeError` with server message.

**GGML model path resolution:**

- If `model` is a bare name like `"ggml-small.en.bin"`, look in `models/` subdirectory (same convention as other providers).
- If `model` is an absolute path, use as-is.
- Add note in profile comments about running `whisper.cpp/scripts/download-ggml-model.sh small.en` to fetch.

---

### 2. Register in Factory

**File:** `app/conversational_harness/providers/factory.py`

- Add import: `from conversational_harness.providers.whisper_cpp import WhisperCppServerASRProvider`
- Add branch in `build_asr()`:

```python
if provider == "whisper-cpp":
    return WhisperCppServerASRProvider(config)
```

---

### 3. Add Profile JSONs

#### `profiles/llamacpp-vulkan-asr.json` (new — primary non-CUDA profile)

```json
{
  "name": "llamacpp-vulkan-asr",
  "description": "Local llama.cpp server with whisper.cpp ASR on Vulkan. For AMD/Intel GPUs or any Vulkan-capable device.",
  "audio": { ... same as cuda profile ... },
  "vad": { "provider": "silero", ... same as cuda profile ... },
  "asr": {
    "provider": "whisper-cpp",
    "base_url": "http://127.0.0.1:8082",
    "model": "ggml-small.en.bin",
    "language": "en",
    "temperature": 0,
    "reject_low_energy_rms": 0.003,
    "reject_low_energy_max_duration_ms": 900,
    "reject_utterance_rms": 0.002,
    "trim_silence_rms": 0.003,
    "trim_silence_frame_ms": 30,
    "timeout_s": 120
  },
  "llm": { "provider": "llamacpp", ... same ... },
  "tts": { "provider": "pocket-tts", ... same ... },
  "turn": { ... same ... }
}
```

#### `profiles/llamacpp-cpu-asr.json` (optional — whisper.cpp CPU fallback)

Same structure with `--gpu disabled` or no GPU flag. Shows users they can run whisper.cpp on CPU too.

---

### 4. Update Install/Build Scripts

**File:** `install.ps1` and `install.sh`

Add section for whisper.cpp:

```powershell
# Optional: Build whisper.cpp with Vulkan support
Write-Host "To build whisper.cpp server for non-CUDA ASR:"
Write-Host "  git clone https://github.com/ggml-org/whisper.cpp"
Write-Host "  cd whisper.cpp"
Write-Host "  cmake -B build -DGGML_VULKAN=ON"
Write-Host "  cmake --build build --config Release"
Write-Host "  copy build\bin\Release\whisper-server.exe .\bin\"
Write-Host "  (or add whisper.cpp\build\bin\Release to your PATH)"
```

For macOS (Metal):
```bash
cmake -B build -DGGML_METAL=ON
```

For CPU-only:
```bash
cmake -B build
```

**Model download script** or README instruction:
```bash
# Download small.en model
bash whisper.cpp/models/download-ggml-model.sh small.en
# Or manually place .bin files in ./models/
```

**File:** `doctor.py`

Add check for whisper.cpp: if profile has `"provider": "whisper-cpp"` but `whisper-server` is not in PATH or `models/ggml-*.bin` not found, print a clear diagnostic.

---

### 5. Add Tests

**File:** `tests/test_whisper_cpp.py` (new)

- **Unit test:** `WhisperCppServerASRProvider` construction reads config correctly.
- **Unit test:** `transcribe_text_input()` yields single final event with passthrough text.
- **Integration test (marked as such):** Start `whisper-server` subprocess with small model, transcribe a known WAV file, verify output contains expected text. Mark with `@pytest.mark.integration` so it's skipped in CI if server binary is absent.
- **Unit test:** `check_status()` returns correct `ProviderStatus` shape.
- **Unit test:** `load()` / `unload()` lifecycle state transitions.
- **Mocked HTTP test:** Mock `aiohttp.ClientSession.post` to return known JSON, verify `transcribe_audio()` yields correct transcript.

---

### 6. Update Documentation

**File:** `README.md`

- Add whisper.cpp as optional ASR backend in feature list.
- Add "Installing whisper.cpp" section under step-by-step.
- Reference the new profiles.

**File:** `docs/architecture.md`

- Update provider diagram/table to include whisper-cpp.
- Add note: whisper.cpp runs as managed subprocess (like llama.cpp server).

**File:** `AGENTS.md`

- Update ASR line: "ASR: faster-whisper default, whisper.cpp server as non-CUDA option."
- Add whisper.cpp-specific guidance if any divergence from faster-whisper behavior.

---

### 7. Future (Not in Initial Scope)

- **Streaming/partials:** whisper.cpp server supports `/v1/stream` endpoint. Add optional partial transcript support in v2.
- **Auto-download models:** Script to download GGML models from HuggingFace.
- **Prebuilt binaries:** Reference prebuilt whisper-server binaries for Windows/Linux (once stable).
- **Graceful fallback:** If whisper.cpp server fails to start, fall back to faster-whisper CPU. Needs config option `fallback_provider`.

---

## Notes

- **faster-whisper stays default.** All existing profiles unchanged. whisper.cpp profiles are opt-in.
- **Existing noise rejection in `main.py`** (RMS gates, silence trimming) is provider-agnostic — works as-is with whisper.cpp.
- **Existing Silero VAD** handles speech boundaries — whisper.cpp doesn't need its own VAD.
- **No changes to orchestrator.py or main.py** — ASRProvider protocol is the integration boundary.
- **Subprocess lifecycle mirrors `llamacpp.py`** — that file is the template for how to manage an external server process.
- **PyPI package whisper-cpp-python** exists as alternative to subprocess management — worth evaluating but start with server mode for lowest latency.
- **Test with `--processors 1`** to avoid known whisper.cpp Vulkan multi-processor crash on AMD GPUs.
