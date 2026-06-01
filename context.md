# Code Context — ASR Audit

## Files Retrieved

1. `app/conversational_harness/providers/base.py` (lines 73-92) — `ASRProvider` Protocol, `TranscriptEvent`, `ProviderCapabilities`, `ProviderStatus`, `ProgressCallback`
2. `app/conversational_harness/providers/faster_whisper.py` (full, 170 lines) — `FasterWhisperASRProvider` implementation
3. `app/conversational_harness/providers/mock.py` (lines 44-81) — `MockASRProvider` for smoke tests
4. `app/conversational_harness/providers/unavailable.py` (lines 17-45) — `UnavailableProvider` fallback
5. `app/conversational_harness/providers/factory.py` (lines 19-21, 70-76) — `build_asr()` factory, `ProviderBundle`
6. `app/conversational_harness/orchestrator.py` (lines 90-185) — `handle_text_turn()`, `handle_audio_turn()` call ASR
7. `app/conversational_harness/main.py` (lines 396-680) — WebSocket handler: ASR preload, VAD pipeline, noise rejection, turn dispatch
8. `app/conversational_harness/audio.py` (full) — PCM conversion utilities
9. `app/conversational_harness/audio_frames.py` (lines 57-125) — Frame parsing, RMS metering, silence trimming
10. `profiles/llamacpp-cuda-asr.json` (full) — Default profile with CUDA faster-whisper ASR
11. `profiles/llamacpp-local.json` (full) — CPU faster-whisper ASR profile
12. `profiles/mock-local.json` (full) — Mock ASR profile
13. `profiles/llamacpp-kokoro-onnx.json` (full) — CPU faster-whisper ASR profile
14. `requirements.txt` (line 2, 8) — `faster-whisper==1.2.1`, `nvidia-cublas-cu12`
15. `tests/test_faster_whisper.py` (full) — Test suite covering load, transcribe, config overrides, sample rate validation
16. `AGENTS.md` (lines 5-6, 39-45) — Design decisions: ASR stack, hallucination mitigation
17. `docs/architecture.md` (lines 50-105) — Architecture docs: provider contracts, data flow diagrams

---

## 1. ASR Provider Interface (Contract)

**File:** `app/conversational_harness/providers/base.py`, lines 73-92

```python
class ASRProvider(Protocol):
    @property
    def status(self) -> ProviderStatus: ...
    async def check_status(self) -> ProviderStatus: ...
    async def load(self) -> ProviderStatus: ...
    async def transcribe_text_input(self, text: str) -> AsyncIterator[TranscriptEvent]: ...
    async def transcribe_audio(
        self, pcm_s16le: bytes, sample_rate: int, progress: ProgressCallback | None = None
    ) -> AsyncIterator[TranscriptEvent]: ...
    async def unload(self) -> ProviderStatus: ...
```

**Key type — `TranscriptEvent` (line 30-33):**
```python
@dataclass(frozen=True)
class TranscriptEvent:
    text: str
    final: bool
```

**Key type — `ProgressCallback` (line 46):**
```python
ProgressCallback = Callable[[str, dict], Awaitable[None]]
```

**Key type — `ProviderCapabilities` (lines 14-21):**
```python
@dataclass(frozen=True)
class ProviderCapabilities:
    supports_partials: bool = False
    supports_streaming_tts: bool = False
    supports_barge_in: bool = False
    requires_gpu: bool = False
    languages: tuple[str, ...] = ("en",)
```

**Contract summary:**
- `load()` — eager model load. Called preemptively on WebSocket connect to warm the model before first utterance.
- `transcribe_text_input(text)` — synchronous text passthrough for text-mode turns. Yields `TranscriptEvent(text, final=True)`. Mock provider yields partial tokens.
- `transcribe_audio(pcm_s16le, sample_rate, progress)` — PCM s16le bytes at given sample rate. Returns async iterable of `TranscriptEvent`. Each event may be `final=False` (partial) or `final=True` (final). The orchestrator only uses the last `final=True` event as the definitive transcript. `progress` callback emits `asr.progress` events with `{"stage": ..., "message": ...}`.
- `check_status()` — health check, reports ready state.
- `unload()` — release model resources.
- `status` property — returns current `ProviderStatus` (name, kind, ready, message, capabilities).

---

## 2. All ASR Provider Files & Implementations

### 2a. FasterWhisperASRProvider (`providers/faster_whisper.py`)

**Class:** `FasterWhisperASRProvider(ASRProvider)` (line 14)

**Constructor config keys read (lines 17-32):**
| Key | Default | Purpose |
|---|---|---|
| `model` | `"large-v3-turbo"` | HuggingFace model ID or path |
| `device` | `"auto"` | `"cuda"`, `"cpu"`, or `"auto"` |
| `compute_type` | `"auto"` | `"float16"`, `"int8_float16"`, `"int8"`, `"auto"` |
| `language` | `"en"` | Language code for transcription |
| `beam_size` | `1` | Beam search width |
| `vad_filter` | `False` | faster-whisper's built-in VAD filter |
| `initial_prompt` | `None` | Optional prompt for style biasing |
| `condition_on_previous_text` | `False` | Hallucination mitigation |
| `temperature` | `0` | Sampling temperature (0 = greedy) |
| `compression_ratio_threshold` | `2.4` | Hallucination filter |
| `log_prob_threshold` | `-0.5` | Hallucination filter |
| `no_speech_threshold` | `0.2` | Silence rejection |
| `suppress_tokens` | `None` | Token IDs to suppress |
| `timeout_s` | `120` | Model load + transcription timeout |
| `_model` | `None` | Internal: pre-loaded WhisperModel (for testing) |

**Key behavior:**
- **Lazy model loading**: `_ensure_model()` called on first `transcribe_audio()`. Creates `WhisperModel(model_name, device=device, compute_type=compute_type)` via `faster_whisper.WhisperModel`.
- **Requires 16kHz input**: raises `ValueError` if `sample_rate != 16000`.
- **Converts PCM to float32**: uses `pcm_s16le_to_float32()` from `audio.py`.
- **Runs in thread**: `asyncio.to_thread(self._transcribe_blocking, ...)` to avoid blocking the event loop.
- **Timeout**: wrapped in `asyncio.wait_for(..., timeout=self.timeout_s)`.
- **emits progress**: fires `asr.progress` events at stages: `"loading"`, `"loaded"`, `"segment"`, `"complete"`.
- **Text input**: passthrough — immediately yields `TranscriptEvent(text=stripped, final=True)`.
- **No partials**: only yields a single `TranscriptEvent` with `final=True` at the end.

**Thread-safe progress emission (line 175):**
```python
def _emit_progress_threadsafe(
    self, loop, progress, stage, message
) -> None:
    if not progress:
        return
    asyncio.run_coroutine_threadsafe(
        progress("asr.progress", {"stage": stage, "message": message}),
        loop
    )
```

**`_transcribe_blocking()` (lines 123-170):**
```python
transcribe_options = {
    "language": self.language,
    "beam_size": self.beam_size,
    "vad_filter": self.vad_filter,
    "initial_prompt": self.initial_prompt,
    "condition_on_previous_text": self.condition_on_previous_text,
    "temperature": self.temperature,
    "compression_ratio_threshold": self.compression_ratio_threshold,
    "log_prob_threshold": self.log_prob_threshold,
    "no_speech_threshold": self.no_speech_threshold,
}
if self.suppress_tokens is not None:
    transcribe_options["suppress_tokens"] = self.suppress_tokens
segments, _info = self._model.transcribe(audio, **transcribe_options)
```

### 2b. MockASRProvider (`providers/mock.py`, lines 44-81)

```python
class MockASRProvider(ASRProvider):
    def __init__(self, config: dict): ...
    # text_input: yields word-by-word partials, then final
    # audio: yields single "Mock ASR heard audio input." final
```

### 2c. UnavailableProvider (`providers/unavailable.py`, lines 17-45)

Stub that implements all provider protocols. `transcribe_audio()` and `transcribe_text_input()` raise `RuntimeError` with the unavailable message.

---

## 3. Factory Wiring

**File:** `providers/factory.py`, lines 70-76

```python
def build_asr(config: dict) -> ASRProvider:
    provider = config.get("provider", "mock")
    if provider == "mock":
        return MockASRProvider(config)
    if provider == "faster-whisper":
        return FasterWhisperASRProvider(config)
    return UnavailableProvider("asr", str(provider), ...)
```

**Only two ASR providers are wired:** `mock` and `faster-whisper`. Any unknown name produces `UnavailableProvider`.

**`ProviderBundle` (line 19-21):**
```python
@dataclass
class ProviderBundle:
    vad: VADProvider
    asr: ASRProvider
    llm: LLMProvider
    tts: TTSProvider
```

**Construction entry point (line 101-108):**
```python
def build_providers(config: HarnessConfig) -> ProviderBundle:
    return ProviderBundle(
        vad=build_vad(config.section("vad")),
        asr=build_asr(config.section("asr")),
        llm=build_llm(config.section("llm")),
        tts=build_tts(config.section("tts")),
    )
```

---

## 4. Orchestrator — Where ASR Is Called

### 4a. Text Turn (`orchestrator.py`, lines 90-122)

```python
async def handle_text_turn(self, text: str, mode: str = "chat") -> None:
    ...
    final_transcript = ""
    async for transcript in self.providers.asr.transcribe_text_input(text):
        await self.sink.emit("asr.transcript", ...)
        if transcript.final:
            final_transcript = transcript.text
    ...
    await self._respond_to_transcript(final_transcript, ...)
```

### 4b. Audio Turn (`orchestrator.py`, lines 123-185)

```python
async def handle_audio_turn(self, pcm_s16le: bytes, sample_rate: int, mode: str = "chat") -> None:
    ...
    final_transcript = ""
    try:
        async def progress(event_type: str, payload: dict) -> None:
            await self.sink.emit(event_type, **payload, latency_ms=elapsed_ms(started))

        async for transcript in self.providers.asr.transcribe_audio(
            pcm_s16le, sample_rate, progress
        ):
            await self.sink.emit("asr.transcript", ...)
            if transcript.final:
                final_transcript = transcript.text
    except Exception as exc:
        await self.sink.emit("asr.error", ...)
        await self.sink.emit("turn.finished", reason="asr_error", ...)
        return

    if not final_transcript:
        await self.sink.emit("turn.finished", reason="empty_transcript", ...)
        return

    await self._respond_to_transcript(final_transcript, ...)
```

**Flow:** `handle_audio_turn()` → `asr.transcribe_audio()` → `TranscriptEvent` iterator → picks last `final=True` text → `_respond_to_transcript()` → LLM → TTS.

### 4c. ASR Preload on WebSocket Connect (`main.py`, lines 440-485)

```python
async def warm_asr_on_connect() -> None:
    if not hasattr(providers.asr, "load"):
        return
    await sink.emit("asr.progress", stage="loading", ...)
    try:
        status = await providers.asr.load()
    except Exception as exc:
        await sink.emit("asr.error", message=f"ASR preload failed: {exc}")
        return
```

Pre-loaded eagerly after WebSocket handshake so first utterance doesn't wait for model load.

### 4d. VAD → Audio Pipeline in WebSocket Handler (`main.py`, lines 546-680)

The full audio pipeline before ASR:

1. **Frame ingestion**: `audio.frame` messages parsed via `parse_audio_frame()` → pre-buffer + utterance buffer
2. **VAD processing**: `providers.vad.process_frame(frame)` → `VADEvent` list
3. **On `vad.speech_start`**: Cancel active TTS (barge-in), flush pre-buffer into utterance buffer, set `recording_utterance = True`
4. **On `vad.speech_end`**: Stop recording, get full PCM utterance
5. **Noise rejection chain** (lines 609-669):
   - `min_speech_duration_ms` gate (VAD config) — rejects short transients < 200-300ms
   - `reject_low_energy_rms` + `reject_low_energy_max_duration_ms` (ASR config) — rejects low-energy short utterances
   - `reject_utterance_rms` (ASR config) — floor for all utterances
   - `trim_silence_rms` + `trim_silence_frame_ms` (ASR config) — edge silence trimming via `trim_pcm16_silence()`
6. **Dispatch to orchestrator** (lines 670-675):
   ```python
   if pcm and (not active_turn or active_turn.done()):
       active_turn = asyncio.create_task(
           orchestrator.handle_audio_turn(pcm, audio_stats.expected_sample_rate, mode=recording_mode)
       )
   ```

**Noise rejection config keys are read from `asr` profile section** (`main.py`, lines 411-418):
- `reject_low_energy_rms` (default 0)
- `reject_low_energy_max_duration_ms` (default 0)
- `reject_utterance_rms` (default 0)
- `trim_silence_rms` (default 0)
- `trim_silence_frame_ms` (default `frame_ms`)

---

## 5. Profile/Config Files with ASR Settings

### Default profile: `profiles/llamacpp-cuda-asr.json`
```json
"asr": {
    "provider": "faster-whisper",
    "model": "large-v3-turbo",
    "device": "cuda",
    "compute_type": "float16",
    "language": "en",
    "beam_size": 1,
    "vad_filter": false,
    "condition_on_previous_text": false,
    "temperature": 0,
    "compression_ratio_threshold": 2.4,
    "log_prob_threshold": -0.5,
    "no_speech_threshold": 0.2,
    "reject_low_energy_rms": 0.003,
    "reject_low_energy_max_duration_ms": 900,
    "reject_utterance_rms": 0.002,
    "trim_silence_rms": 0.003,
    "trim_silence_frame_ms": 30,
    "timeout_s": 180
}
```

### `profiles/llamacpp-local.json` — CPU variant
Same structure, `"device": "cpu"`, `"compute_type": "int8"`, `"vad_filter": true`.

### `profiles/mock-local.json` — smoke test variant
```json
"asr": {
    "provider": "mock",
    "supports_partials": true
}
```

### `profiles/llamacpp-kokoro-onnx.json` — CPU ASR
Identical ASR section to `llamacpp-local.json` (faster-whisper, CPU, int8).

**All profiles** use either `"faster-whisper"` or `"mock"` as the ASR provider. No other ASR backend is configured in any profile.

---

## 6. Non-CUDA ASR Backends — Current State

- **CPU int8 via faster-whisper** is fully wired and operational — `profiles/llamacpp-local.json` uses `"device": "cpu", "compute_type": "int8"`.
- **No other ASR backend** (no Whisper.cpp, no Wav2Vec2, no DeepSpeech, no speech-to-text API) exists in the codebase.
- **The only ASR provider types recognized** by `build_asr()` are `"mock"` and `"faster-whisper"`.
- **`AGENTS.md` line 6** explicitly states: "ASR: faster-whisper first, with other STT backends added only behind provider interfaces when locally proven."
- **Adding a new ASR provider** requires:
  1. New class implementing `ASRProvider` protocol
  2. Factory entry in `build_asr()` in `factory.py`
  3. Profile JSON with `"provider": "<your-name>"`

---

## 7. ASR Dependencies in requirements.txt

```
faster-whisper==1.2.1                          # CTranslate2-based Whisper
nvidia-cublas-cu12==12.9.1.4; platform_system == "Windows"   # CUDA runtime for Windows
```

**Install scripts** (`install.ps1` / `install.sh`):
- Create `.venv`, install `requirements.txt`
- No separate ASR-specific install steps
- `start.ps1` adds `.venv/Lib/site-packages/nvidia/*/bin` to `PATH` for CUDA DLL resolution

---

## 8. Design Decisions from AGENTS.md and Docs

From **AGENTS.md**:
- "ASR: faster-whisper first, with other STT backends added only behind provider interfaces when locally proven." (line 6)
- Hallucination mitigation for faster-whisper large-v3-turbo (lines 41-45):
  - Silero profile: `speech_threshold: 0.65`, `neg_threshold: 0.4`, `min_speech_duration_ms: 300`
  - faster-whisper: `condition_on_previous_text: false`, `temperature: 0`, `compression_ratio_threshold: 2.4`, `log_prob_threshold: -0.5`, `no_speech_threshold: 0.2`
  - Pre-ASR guards in `main.py`: `reject_low_energy_rms: 0.003` up to 900ms, `reject_utterance_rms: 0.002`, `trim_silence_rms: 0.003`
- "Keep these values profile-driven. If hallucinations return, tune VAD/energy/trim thresholds before adding phrase-specific text filters" (line 45)

From **architecture.md**:
- Provider protocols are structural typing (`Protocol`), not inheritance. No base class required.
- Lazy model loading — ASR loads on first `transcribe_audio()`.
- `transcribe_audio()` runs in `asyncio.to_thread` to avoid blocking.
- Sample rate is fixed at 16kHz for ASR.
- PCM format is `pcm_s16le` (signed 16-bit little-endian).

---

## Integration Pattern — Adding a New ASR Provider

### Required steps:

1. **Create provider class** implementing `ASRProvider` protocol:
   ```python
   class MyASRProvider:
       async def load(self) -> ProviderStatus: ...
       async def transcribe_text_input(self, text: str) -> AsyncIterator[TranscriptEvent]: ...
       async def transcribe_audio(
           self, pcm_s16le: bytes, sample_rate: int,
           progress: ProgressCallback | None = None
       ) -> AsyncIterator[TranscriptEvent]: ...
       async def check_status(self) -> ProviderStatus: ...
       async def unload(self) -> ProviderStatus: ...
       @property
       def status(self) -> ProviderStatus: ...
   ```

2. **Register in factory** (`factory.py`, `build_asr()`):
   ```python
   if provider == "my-asr":
       return MyASRProvider(config)
   ```

3. **Add profile JSON** with `"provider": "my-asr"` in the `"asr"` section.

4. **Add dependency** to `requirements.txt` if needed.

### What the orchestrator expects:
- PCM data is `pcm_s16le` at 16kHz (the orchestrator enforces this before calling ASR).
- The progress callback fires `asr.progress` events with stages `"loading"`/`"loaded"`/`"segment"`/`"complete"`.
- At least one `TranscriptEvent` with `final=True` must be yielded, or the turn will be discarded as "empty_transcript".
- Partial transcripts (`final=False`) are optional but help UX.
- The provider should handle its own model lifecycle — `load()` for eager warm-up, `unload()` for resource release.
- All methods run in async context. CPU-bound work should use `asyncio.to_thread()`.

### Files that need changes for new ASR provider:
| File | Change |
|---|---|
| `app/.../providers/my_asr.py` | New provider class |
| `app/.../providers/factory.py` | Add `import` + `build_asr()` branch |
| `profiles/my-profile.json` | New or modified profile |
| `requirements.txt` | If new dependencies |
| `tests/test_my_asr.py` | New test file |

### No other files need changes:
- `orchestrator.py` calls `asr.transcribe_audio()` generically — no switch logic.
- `main.py` noise rejection is profile-driven and provider-agnostic.
- `main.py` preloads via `providers.asr.load()` — works for any provider with a `load()` method.
- `base.py` protocol unchanged.

---

## Start Here

Open `app/conversational_harness/providers/faster_whisper.py` — the only real ASR implementation. It shows the full pattern: lazy model loading, thread-offloaded blocking call, progress callback, config-driven options, and PCM conversion. Model a new provider on this structure.

Then `app/conversational_harness/providers/factory.py` line 70 — `build_asr()` is where registration happens.

Then `app/conversational_harness/providers/base.py` lines 73-92 — the protocol contract.
