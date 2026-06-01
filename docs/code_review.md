# Code Review — Converse-AI

Reviewed commits: `9f409fe`–`e74bdf2` (last 3 commits)  
Files changed: `main.py`, `doctor.py`, `providers/base.py`, `providers/faster_whisper.py`, `providers/mock.py`, `tests/test_config.py`, `tests/test_doctor.py`, `tests/test_faster_whisper.py`, `doctor.ps1`, `README.md`  
Date: 2026-05-31

---

## Fixes Worth Doing Now

Priority after second review:

1. Faster-whisper provider state handling around load timeout, unload, status, and failed-load tests.
2. Doctor fatal checks and the llama.cpp loading test.
3. `warm_asr_on_connect()` task lifecycle cleanup.
4. Byte-based utterance overflow calculation.
5. Extract/test the utterance guard chain later.

### [x] 1. `faster_whisper.load()` — `TimeoutError` does not set `_load_error`; detached thread races

**File:** `app/conversational_harness/providers/faster_whisper.py:67`
**Status:** Fixed 2026-05-31.

`asyncio.wait_for` can raise `TimeoutError` while `_ensure_model` keeps running inside the threadpool. The thread may later write `self._model` or `self._load_error` with no synchronisation. In the window between timeout and thread completion, `self.status` returns `ready=True` with a message claiming the model loads on first use — a lie.

```python
# Current
await asyncio.wait_for(asyncio.to_thread(self._ensure_model), timeout=self.timeout_s)
return self.status

# Fix
try:
    await asyncio.wait_for(asyncio.to_thread(self._ensure_model), timeout=self.timeout_s)
except asyncio.TimeoutError:
    self._load_error = f"Model load timed out after {self.timeout_s}s"
    raise
return self.status
```

Nuance: `transcribe_audio` should surface a timeout, but should not always mark the provider as a model-load failure. Only mark `_load_error` there when the model is still unloaded after timeout.

---

### [x] 2. `unload()` never clears `_load_error` when `_model is None`

**File:** `app/conversational_harness/providers/faster_whisper.py:159–163`
**Status:** Fixed 2026-05-31.

The guard `if self._model is not None` prevents `_load_error` from being cleared after a failed load. After a failed load → `unload()` → second `load()`, the status still shows the stale old error until `_ensure_model` succeeds and overwrites it.

```python
# Current
async def unload(self) -> ProviderStatus:
    if self._model is not None:
        logger.info(...)
        self._model = None
        self._load_error = None
    return self.status

# Fix — always clear _load_error
async def unload(self) -> ProviderStatus:
    if self._model is not None:
        logger.info(...)
        self._model = None
    self._load_error = None
    return self.status
```

---

### [x] 3. `check_status` overwrites `_load_error` with import error even when model is already loaded

**File:** `app/conversational_harness/providers/faster_whisper.py:57–62`
**Status:** Fixed 2026-05-31.

If the model loaded successfully but `faster_whisper` is somehow absent at import time during a later `check_status` call, the import failure clobbers `_load_error` and marks the provider broken despite a functioning `self._model`.

```python
# Fix — only probe the import when the model has not been loaded yet
async def check_status(self) -> ProviderStatus:
    if self._model is None:
        try:
            import faster_whisper  # noqa: F401
        except Exception as exc:
            self._load_error = str(exc)
    return self.status
```

---

### [x] 4. Brittle frame-count math in utterance overflow check

**File:** `app/conversational_harness/main.py:561`
**Status:** Fixed 2026-05-31.

```python
if len(utterance_buffer) // len(frame.data) > max_utterance_frames:
```

Second-review note: the exact zero-length crash path is unlikely through normal input because `parse_audio_frame` rejects empty/mis-sized payloads. The underlying issue still stands: the overflow guard should not depend on the most recent frame length. Compare byte length directly instead.

```python
# Fix — compare byte length directly
expected_frame_bytes = bytes_per_ms * audio_stats.expected_frame_ms
if len(utterance_buffer) > max_utterance_frames * expected_frame_bytes:
```

---

### [x] 5. Doctor: port-in-use prints `[WARN]`, exits 0 — misleads CI and users

**File:** `app/conversational_harness/doctor.py:~192`
**Status:** Fixed 2026-05-31.

All failed checks use the same `WARN` marker. A blocked port (which prevents startup) looks identical to "Vulkan tooling not found" (advisory). Doctor exits `0` even when the server cannot start, breaking `&&`-chained CI commands.

```python
# Fix
FATAL_CHECKS = {"Python", "Profile", "fastapi", "uvicorn", "Harness port"}

for check in checks:
    if not check.ok and check.name in FATAL_CHECKS:
        marker = "ERR"
    elif not check.ok:
        marker = "WARN"
    else:
        marker = "OK"
    print(f"[{marker}] {check.name}: {check.detail}")

if any(not check.ok for check in checks if check.name in FATAL_CHECKS):
    sys.exit(1)
```

---

### [x] 6. `warm_asr_on_connect` task leaked on WebSocket disconnect

**File:** `app/conversational_harness/main.py:500`
**Status:** Fixed 2026-05-31.

```python
asyncio.create_task(warm_asr_on_connect())
```

This task is untracked. On disconnect, the `except WebSocketDisconnect` handler cancels `sender_task` and `receiver_task` but this orphan keeps running, emitting events into the queue that was just discarded from `ACTIVE_QUEUES`. Events accumulate silently and the task holds a model reference until the event loop is torn down.

```python
# Fix — track and cancel inside sender()
async def sender() -> None:
    warm_task = asyncio.create_task(warm_asr_on_connect())
    try:
        while True:
            event = await queue.get()
            await websocket.send_json(event)
    finally:
        warm_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await warm_task
```

---

### [x] 7. `test_faster_whisper_load_reports_failure` — missing assertions on provider state

**File:** `tests/test_faster_whisper.py:29–40`
**Status:** Fixed 2026-05-31.

The monkeypatched `fail()` bypasses the real `_ensure_model` body that sets `_load_error`. After `load()` raises, `provider._load_error is None` and `provider.status.ready is True`. The test catches the exception but does not verify that the provider correctly reports failure afterward.

```python
# Add after catching RuntimeError:
assert provider._load_error is not None
assert not provider.status.ready
```

Note: the monkeypatched `fail` must also set `provider._load_error` to match the real `_ensure_model` contract, or the test should drive the real failure path.

---

### [x] 8. `test_llamacpp_loading` uses wrong-shaped payload and same dict for both URLs

**File:** `tests/test_doctor.py:19–26`
**Status:** Fixed 2026-05-31.

The lambda ignores the URL, returning `{"error": {"message": "Loading model"}}` (no `status` key) for both `/health` and `/v1/models`. The test passes via early-exit on the missing `status != "ok"` branch, not because the second fetch is handled correctly. The payload also doesn't match what a real llama.cpp server returns when loading.

```python
# Fix
def test_llamacpp_loading(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "loading", "error": {"message": "Loading model"}}
        raise AssertionError(f"/v1/models must not be called: {url}")

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)
    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:8080"})

    assert not check.ok
    assert "not ready" in check.detail
    assert "Loading model" in check.detail
```

---

### [x] 9. `safe_provider_status` — redundant `getattr` that can never fall back

**File:** `app/conversational_harness/doctor.py:63`
**Status:** Fixed 2026-05-31.

```python
name=getattr(provider, "__class__", type(provider)).__name__,
```

`__class__` is a data descriptor present on every Python object. The fallback to `type(provider)` can never trigger. This implies `__class__` might be absent, which is misleading.

```python
# Fix
name=type(provider).__name__,
```

---

## Optional Improvements

### O1. Extract `_apply_utterance_guards` helper from `main.py`

`main.py:597–663` contains a ~70-line inline guard chain with 5 recomputations of `duration_ms = len(pcm) // max(bytes_per_ms, 1)` and implicit `pcm = b""` semantics for rejection. Extracting this into a named function makes it independently testable and clarifies the guard order.

Suggested signature:
```python
async def _apply_utterance_guards(
    pcm: bytes,
    *,
    bytes_per_ms: int,
    min_speech_duration_ms: int,
    reject_low_energy_rms: float,
    reject_low_energy_max_duration_ms: int,
    reject_utterance_rms: float,
    trim_silence_rms: float,
    trim_silence_frame_ms: int,
    sample_rate: int,
    recording_mode: str,
    sink,
) -> bytes:
    """Returns filtered/trimmed PCM, or b'' if rejected."""
```

---

### O2. Add `@runtime_checkable` to Protocol classes in `base.py`

`base.py:50–100` — `VADProvider`, `ASRProvider`, `LLMProvider`, `TTSProvider` are `Protocol` subclasses without `@runtime_checkable`. `isinstance(provider, ASRProvider)` raises `TypeError` at runtime. Tests cannot assert protocol conformance. Add the decorator if runtime checks are wanted; otherwise document that the protocols are structural-only.

---

### O3. Cross-platform `find_port_owner` in `doctor.py`

`doctor.py:97–118` silently returns `None` on Linux/macOS. The port-in-use message mentions `stop.ps1` (Windows-only). Add a Unix path using `ss` (Linux) or `lsof` (macOS/Linux):

```python
def find_port_owner(port: int) -> dict[str, str] | None:
    system = platform.system()
    if system == "Windows":
        return _find_port_owner_windows(port)
    if system in ("Linux", "Darwin"):
        for cmd in (
            ["ss", "-tlnp", f"sport = :{port}"],
            ["lsof", "-i", f":{port}", "-sTCP:LISTEN", "-n", "-P"],
        ):
            try:
                out = subprocess.check_output(cmd, text=True, stderr=subprocess.DEVNULL)
                if out.strip():
                    return {"pid": "see output", "command": out.strip()[:120]}
            except Exception:
                continue
    return None
```

---

### O4. Mock ASR `transcribe_audio` produces fixed string, no partials

`mock.py:81–85` — `transcribe_audio` always yields `"Mock ASR heard audio input."` with no partials, while `transcribe_text_input` yields word-by-word partials. This means `supports_partials=True` is never exercised on the audio path, and orchestrator bugs in audio-turn partial accumulation are invisible in mock-mode tests.

Suggested fix: emit at least one partial event and derive the transcript text from audio duration:
```python
async def transcribe_audio(self, pcm_s16le: bytes, sample_rate: int, progress=None):
    duration_s = len(pcm_s16le) / max(sample_rate * 2, 1)
    text = f"Mock audio input ({duration_s:.1f}s)"
    yield TranscriptEvent(text=text, final=False)
    await asyncio.sleep(0.02)
    yield TranscriptEvent(text=text, final=True)
```

---

### O5. Add tests for VAD/ASR guard paths (zero coverage)

All five guard branches in the `vad.speech_end` handler have no tests. Per AGENTS.md testing guidelines, canned audio tests are required. Suggested test targets:

| Guard | Condition to test |
|---|---|
| `min_speech_duration_ms` | Short PCM → `vad.speech_rejected` fired, ASR skipped |
| `reject_low_energy_rms` | Low-RMS short PCM rejected; long low-RMS PCM passes |
| `reject_utterance_rms` | Whole-utterance low-RMS → rejected |
| `trim_pcm16_silence` | Leading/trailing silence removed; all-silent → `b""` |
| `pre_buffer` | Pre-speech frames included at utterance start |

Extracting the guard chain (O1) is a prerequisite for unit-testing these without a live WebSocket.

---

### O6. Provider bundle rebuilt on every `/api/status` call

`main.py:302–306` — `provider_statuses_payload` calls `build_provider_bundle` on every HTTP status request, constructing a fresh provider instance. This instance is separate from the live WebSocket session's providers, so ASR load state from the session is never reflected in the HTTP status endpoint. Consider a module-level singleton bundle or passing the session's providers into the status helper.

---

### O7. Validate `bytes_per_ms > 0` at startup

`main.py:422` — `bytes_per_ms = audio_stats.expected_sample_rate * 2 // 1000`. For `sample_rate < 500`, this rounds to `0`. The `max(bytes_per_ms, 1)` guard prevents `ZeroDivisionError` but silently produces completely wrong duration values on every guard check. Add an early assertion:

```python
if bytes_per_ms == 0:
    raise ValueError(
        f"sample_rate {audio_stats.expected_sample_rate} Hz is too low; "
        "bytes_per_ms rounds to zero. Minimum supported: 500 Hz."
    )
```

---

## Deferred / Out of Scope

| Item | Reason |
|---|---|
| Thread data race on `_model`/`_load_error` | CPython GIL makes this safe today; free-threading (`--disable-gil`) is experimental — revisit when it stabilises |
| `websocket_events` monolith refactor (~300 lines, 20+ closure variables) | Valid structural debt but a large standalone task; defer to a dedicated refactor |
| Missing AGENTS.md integration tests (barge-in, TTS smoke, latency, degraded mode) | Real gap per guidelines but scope-level work, not a quick fix |
| `collect_checks` broad `except Exception` in doctor | Low-risk in a CLI tool; add `logger.debug(exc_info=True)` if desired, not urgent |
