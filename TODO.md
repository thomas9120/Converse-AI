# TODO

## Code Review Findings

### Bugs

#### 1. Double initialization in `main.py` (lines 37–71) — **FIXED**
Removed the first init block and the dead throwaway-ProviderBundle shutdown logic. The sole init
block now lives after `app = FastAPI(...)`.

#### 2. Unsafe thread-to-event-loop progress dispatch — **FIXED**
Replaced `loop.call_soon_threadsafe(asyncio.create_task, ...)` with
`asyncio.run_coroutine_threadsafe(..., loop)` in both `faster_whisper.py:152` and
`pocket_tts.py:179`.

#### 3. `cancel_tts` does not await cancelled tasks (`orchestrator.py:69–75`) — **FIXED**
Added `await asyncio.gather(*active, return_exceptions=True)` after the cancel loop so that
stale TTS streams complete before the next turn starts.

#### 4. `_stream_tts_after` silently swallows TTS task failures (`orchestrator.py:211–215`) — **FIXED**
Replaced bare `pass` with `logger.warning(...)`. `CancelledError` (a `BaseException`) is not
caught, so expected cancellations stay silent.

#### 5. `build_provider_bundle` mutates the config dict in place (`factory.py:110`) — **FIXED**
Fixed `HarnessConfig.section()` in `config.py` to `return dict(value)` instead of `return
value`, so callers always get a copy. This fixes both the factory.py mutation and prevents
future callers from silently mutating config.

---

### Performance

#### 6. `LlamaCppProvider.resolve_model` makes an HTTP round-trip on every LLM request (`llamacpp.py:103, 138–149`)
When `model == "auto"`, `stream_response` calls `resolve_model()` which makes a fresh
`/v1/models` GET on every user turn. Cache the resolved model ID after first resolution and
refresh only on `check_status()`.

#### 7. `provider_statuses_payload` rebuilds all providers on every call (`main.py:242–252`)
Called by both `/api/status` and `broadcast_providers_status()` (fires on every TTS
preset/voice change). Each call reconstructs all 4 provider objects from scratch. VAD, ASR, and
LLM providers should be singletons; only the TTS provider needs rebuilding on preset changes.

#### 8. `compute_pcm16_level` uses pure-Python loops over audio samples (`audio_frames.py:105–106`)
```python
peak = max(abs(s) for s in samples) / 32768
mean_square = sum((s / 32768) ** 2 for s in samples) / len(samples)
```
Runs at 10 Hz on every audio frame. NumPy is already a dependency. Replace with:
```python
arr = np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0
peak = float(np.abs(arr).max())
rms = float(np.sqrt(np.mean(arr ** 2)))
```

---

### Maintainability

#### 9. `HarnessConfig.section()` returns a mutable reference into the internal dict (`config.py:23`)
`section()` returns `self.raw.get(name, {})` directly. Callers can (and do — see #5) mutate the
config in place. Return `dict(value)` to always hand back a copy.

#### 10. Duplicated LLM-stream + TTS-flush loop (`orchestrator.py:134–195`)
The inner token-streaming and TTS-flushing logic is copy-pasted between `handle_continue` and
`_respond_to_transcript`. Extract a private `_stream_llm_and_tts(messages, started, turn_id)`
helper to eliminate the duplication.

#### 11. `set_server_defaults` uses a redundant identity mapping (`runtime_settings.py:156–168`)
Ten of eleven entries map `"foo" -> "foo"`. Only `max_tokens -> n_predict` differs. Simplify to
iterate `SAMPLER_KEYS` directly and handle the one special case explicitly.

#### 12. Hook sets are typed `set[Any]` (`main.py:40–42, 69–71`)
```python
TTS_CANCEL_HOOKS: set[Any] = set()
```
The actual callable signatures are known. Use specific `Callable` types to catch errors earlier.

#### 13. `TTSRuntimeManager.select_preset` / `select_voice` call `describe()` outside the lock (`tts_runtime.py:127, 139`)
The lock is released after mutation, then `describe()` re-acquires it. Between the two
acquisitions, another coroutine could observe a partially-updated state. Either inline the
describe logic inside the existing lock scope, or document the accepted risk.

#### 14. `VADEvent.type` field shadows the Python built-in `type` (`base.py:49`)
Rename to `event_type` or `kind` for clarity and to avoid shadowing the built-in.

---

### Style / Minor

#### 15. Import ordering in `faster_whisper.py` (line 10)
`logger = logging.getLogger(__name__)` appears before a project-level import. Move all imports
above the logger assignment per PEP 8.

#### 16. `should_flush_tts` parameter names are inconsistent with profile keys (`orchestrator.py:274`)
Parameters are named `limit` and `minimum`; the profile JSON uses `tts_chunk_chars` and
`min_tts_chars`. Rename to match, and add a short docstring.

#### 17. PNG CRC not validated in `parse_character_png` (`runtime_settings.py:107–125`)
Chunk CRCs are parsed but not checked. Corrupted cards silently produce wrong data until the
payload fails to decode. Add a `zlib.crc32` check per chunk (low priority for a local tool).

#### 18. `UnavailableProvider` multiple-Protocol inheritance is undocumented (`unavailable.py:17`)
Inheriting from all 4 `Protocol` classes works but is unusual. Add a comment explaining why the
class implements all roles (single catch-all for unknown provider names in the factory).
