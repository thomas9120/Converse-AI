# Code Review: Conversational AI Harness

## Security Issues

### 1. Command Injection in `doctor.py:110-121`

```python
output = subprocess.check_output(
    ["powershell", "-NoProfile", "-Command",
     f"(Get-CimInstance Win32_Process -Filter \"ProcessId={pid}\").CommandLine"],
    ...
)
```

`pid` comes from `netstat` output parsing and is never validated as numeric. If a crafted line produces a non-numeric `pid`, it gets interpolated into a PowerShell command. Add `int(pid)` validation before use.

### 2. XSS via `innerHTML` in `app.js:93-98`

```javascript
node.innerHTML = `
  <strong>${provider.kind}: ${provider.name}</strong>
  <p>${provider.message}</p>
`;
```

Provider messages (which can contain user-configured values like model names from `llamacpp.py:83`) are injected via `innerHTML` without sanitization. A crafted model name like `<img onerror=alert(1) src=x>` would execute. Use `textContent` for all dynamic data or sanitize before insertion.

### 3. SSRF via Configurable URLs in `kokoro_onnx.py:212-226`

`model_url` and `voices_url` are read from profile JSON and fetched without validation. A malicious profile could point these at `http://169.254.169.254/...` (cloud metadata) or internal services. Validate URL schemes and block private/internal IP ranges, or at minimum document that profile files are trusted input.

### 4. No WebSocket Origin Validation in `main.py:183`

`websocket.accept()` is called without checking the `Origin` header. Any local web page could connect to the WebSocket and send commands. Add origin validation:

```python
origin = websocket.headers.get("origin", "")
if origin and origin not in ("http://127.0.0.1:7860", "http://localhost:7860"):
    await websocket.close(code=1008)
    return
```

### 5. Unbounded Memory Growth in `orchestrator.py:114,135`

`self.state.messages` is a list that grows indefinitely with each turn. A long-running session or a client spamming `user.text` will cause OOM. Add a configurable max history window (e.g., keep last N turns or last N tokens).

### 6. No Rate Limiting or Input Size Limits

No limit on WebSocket message rate, text payload size, or number of concurrent WebSocket connections. A single client could open many connections or send huge messages to exhaust resources.

---

## Reliability Issues

### 7. Exception Swallowing in `orchestrator.py:154-158`

```python
if previous is not None:
    try:
        await previous
    except Exception:
        pass
```

All exceptions from previous TTS tasks (including `CancelledError` subclasses other than `asyncio.CancelledError`) are silently discarded. Log these at minimum, and re-raise `CancelledError`.

### 8. Fragile Utterance Length Check in `main.py:278`

```python
if len(utterance_buffer) // len(frame.data) > max_utterance_frames:
```

This divides total buffer bytes by the *last* frame's data length. If frame sizes vary (even by one byte), this produces incorrect counts. Track frame count directly instead.

### 9. Thread Spawn Per TTS Call in `pocket_tts.py:153`

```python
threading.Thread(target=worker, daemon=True).start()
```


### 11. Blocking Download in Event Loop via `kokoro_onnx.py:128`

`_ensure_model` calls `_download_asset` which uses synchronous `httpx.Client`. When called from `load()` via `run_in_executor`, this is fine. But if called from `stream_audio_with_progress`, the `run_in_executor(None, self._ensure_model)` is correct — just make sure the synchronous path is never accidentally called from the event loop directly.

---

## Code Quality Improvements

### 12. Protocol Misuse in `providers/base.py`

`VADProvider`, `ASRProvider`, `LLMProvider`, `TTSProvider` are defined as `Protocol` classes but concrete providers *inherit* from them (e.g., `class SileroVADProvider(VADProvider)`). Protocols are for structural subtyping — you don't inherit from them. Either use abstract base classes (`ABC`) or remove the inheritance from concrete providers.

### 13. Module-Level Singletons in `main.py:24-28`

```python
BASE_CONFIG = load_config()
TTS_MANAGER = TTSRuntimeManager(BASE_CONFIG, load_tts_presets())
ACTIVE_QUEUES: set[...] = set()
```

These are initialized at import time, making testing difficult and preventing multiple instances. Use FastAPI's dependency injection or a lifespan state object.

### 14. Dual Status Pattern

Providers have both a `status` property and a `check_status()` async method that can return different results. This is confusing. Consolidate into a single async method, or make the property clearly a cached value.

### 15. Inefficient Audio Level Computation in `audio_frames.py:98-107`

```python
samples = struct.unpack(f"<{sample_count}h", data)
peak = max(abs(sample) for sample in samples) / 32768
```

Pure Python computation on every 30ms frame (480 samples at 16kHz). The project already depends on numpy — use it here:

```python
arr = np.frombuffer(data, dtype="<i2")
peak = np.max(np.abs(arr)) / 32768
rms = np.sqrt(np.mean((arr / 32768.0) ** 2))
```

### 16. Deprecated `createScriptProcessor` in `app.js:406`

`context.createScriptProcessor(2048, 1, 1)` is deprecated and runs on the main thread, causing audio glitches under load. Migrate to AudioWorklet for production use.

### 17. `next()` Without Default in `tts_runtime.py:76`

```python
return next(item for item in self.presets if item.id == self._selected_id)
```

If `_selected_id` doesn't match any preset, this raises an unhandled `StopIteration`. Use `next(..., None)` with an explicit check.

### 18. No Profile Schema Validation in `config.py`

Any JSON file is accepted as a profile. Missing keys propagate `None` values deep into providers. Add basic schema validation or at minimum required-key checks.

### 19. `compute_pcm16_level` Edge Case in `audio_frames.py:103`

```python
if not samples:
    return {"rms": 0.0, "peak": 0.0}
```

This check is unreachable because `sample_count` is already guaranteed to be positive by the `data` length check. Dead code, but harmless.

---

## Summary

The architecture is clean and well-aligned with the AGENTS.md design principles — providers are properly swappable, event streams are used throughout, and profile-based configuration keeps model specifics out of orchestration logic. The main areas to address:

| Priority | Issue | Files |
|----------|-------|-------|
| **High** | XSS via innerHTML | `app.js` |
| **High** | Command injection (pid validation) | `doctor.py` |
| **High** | Unbounded message history | `orchestrator.py` |
| **Medium** | SSRF via profile URLs | `kokoro_onnx.py` |
| **Medium** | No WebSocket origin check | `main.py` |
| **Medium** | No rate/size limits | `main.py` |
| **Medium** | Exception swallowing in TTS chain | `orchestrator.py` |
| **Medium** | No graceful shutdown | `main.py` |
| **Low** | Protocol/ABC confusion | `providers/base.py` |
| **Low** | Module-level singletons | `main.py` |
| **Low** | Deprecated ScriptProcessor | `app.js` |
| **Low** | Inefficient audio level computation | `audio_frames.py` |
