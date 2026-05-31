# Code Context

## Files Retrieved

1. `app/conversational_harness/providers/base.py` (full, 105 lines) — Provider protocols, data types, capabilities
2. `app/conversational_harness/providers/factory.py` (full, 120 lines) — Provider construction, `ProviderBundle`, `build_*()` functions
3. `app/conversational_harness/providers/llamacpp.py` (full, 170 lines) — LLM provider, OpenAI-compatible API client
4. `app/conversational_harness/providers/mock.py` (full, 135 lines) — Mock providers for testing
5. `app/conversational_harness/orchestrator.py` (full, 305 lines) — Core conversation loop
6. `app/conversational_harness/main.py` (full, 460 lines) — FastAPI app, WebSocket handler, all HTTP endpoints
7. `app/conversational_harness/events.py` (full, 28 lines) — EventSink protocol
8. `app/conversational_harness/config.py` (full, 45 lines) — Profile loading
9. `app/conversational_harness/runtime_settings.py` (full, 340 lines) — Runtime settings, character cards, memory
10. `app/conversational_harness/tts_runtime.py` (full, 160 lines) — TTS preset/runtime management
11. `app/conversational_harness/__init__.py` — Version only
12. `requirements.txt` (full, 17 lines) — Dependencies
13. `profiles/llamacpp-cuda-asr.json`, `llamacpp-local.json`, `mock-local.json` — Profile JSON files
14. `app/static/app.js` — Frontend WebSocket event handling (grep for event types)

## Key Code

### LLMProvider Protocol (`providers/base.py:81-88`)
```python
class LLMProvider(Protocol):
    @property
    def status(self) -> ProviderStatus: ...
    async def check_status(self) -> ProviderStatus: ...
    async def stream_response(self, messages: list[dict[str, str]]) -> AsyncIterator[str]: ...
```
Only streams text tokens. **No tool/function calling support.** Messages are `list[dict[str, str]]` — content is always a string, never structured.

### LlamaCppProvider (`providers/llamacpp.py`)
- Uses **OpenAI-compatible chat completions** at `{base_url}/v1/chat/completions` with `stream: True`
- Parses SSE `data:` lines, extracts `delta.content` only
- Has `set_runtime_settings()` for runtime sampler overrides
- `_build_sampler()` merges server defaults → profile defaults → user overrides
- **Does NOT send `tools` or `functions` in the payload** — pure text chat
- Model resolution via `/v1/models` endpoint
- Health check via `/health` endpoint

### ConversationOrchestrator (`orchestrator.py`)
- Core flow: `handle_text_turn()` / `handle_audio_turn()` → `_respond_to_transcript()` → `_stream_llm_and_tts()`
- `_stream_llm_and_tts()` iterates LLM tokens, buffers sentences, flushes TTS chunks
- `_llm_messages()` assembles `[system_prompt, ...conversation_messages]`
- Turn state per mode (`chat` / `companion`) in `TurnState` dataclass
- `handle_continue()` pops last assistant message, uses it as prefix
- No tool-call interception point exists yet

### ProviderBundle (`providers/factory.py:15-35`)
```python
@dataclass
class ProviderBundle:
    vad: VADProvider
    asr: ASRProvider
    llm: LLMProvider
    tts: TTSProvider
```
Four providers, each built by `build_*()` factory functions from config dicts.

### Provider Factory Pattern (`providers/factory.py`)
Each provider type has a `build_X(config: dict) -> XProvider` function:
- Reads `config["provider"]` string → dispatches to concrete class
- Unknown provider → `UnavailableProvider`
- TTS has extensible `TTS_PROVIDER_BUILDERS` dict for registration

### Event System (`events.py`, `orchestrator.py:QueueEventSink`)
```python
class EventSink:
    async def emit(self, event_type: str, **payload: Any) -> None: ...
```
- `QueueEventSink` puts `{"type": ..., "ts": ..., "payload": ...}` into `asyncio.Queue`
- Backend → frontend via WebSocket `/ws/events`
- Events are free-form strings + kwargs — easy to add new types

### WebSocket Event Types (from `main.py` receiver + `app.js`)
**Inbound (frontend → backend):**
`user.text`, `user.continue`, `system_prompt.update`, `conversation.clear`, `tts.cancel`, `vad.speech_start`, `audio.frame`, `ping`

**Outbound (backend → frontend):**
`profile.loaded`, `providers.status`, `settings.updated`, `turn.started`, `turn.finished`, `turn.error`, `turn.cancelled`, `asr.started`, `asr.transcript`, `asr.progress`, `asr.error`, `asr.buffer_warning`, `asr.audio_trimmed`, `llm.first_token`, `llm.token`, `tts.first_chunk`, `tts.audio`, `tts.progress`, `tts.error`, `tts.cancelled`, `vad.speech_start`, `vad.speech_end`, `vad.speech_rejected`, `vad.probability`, `vad.error`, `audio.input_level`, `audio.frame_error`, `conversation.cleared`, `conversation.seeded`, `pong`

### Config/Profile System (`config.py`)
- `HarnessConfig(path, raw)` — frozen dataclass wrapping a JSON dict
- `config.section("llm")` → returns `dict` for that top-level key
- `DEFAULT_PROFILE = profiles/llamacpp-cuda-asr.json`
- `HARNESS_PROFILE` env var overrides profile path
- Profile JSON structure: `{name, description, audio, vad, asr, llm, tts, turn}`

### Runtime Settings (`runtime_settings.py`)
- Singleton `RUNTIME_SETTINGS` in `main.py`, persisted to `user_settings.json`
- Three-tier sampler merge: server defaults (`/props`) → profile defaults → user overrides
- `effective_system_prompt()` layers: character card → additional instructions → manual textarea
- `apply_patch()` for partial updates from API/WebSocket
- `CompanionSettings` for companion mode with its own overrides

### Dependencies (`requirements.txt`)
```
fastapi, faster-whisper, httpx, kokoro-onnx, misaki, num2words, numpy,
nvidia-cublas-cu12 (Windows), onnxruntime, pocket-tts, spacy, pytest,
silero-vad, uvicorn, websockets
```
**No MCP library. No `mcp`, `anthropic`, or `openai` packages.** Only `httpx` for HTTP.

## Architecture

```
Browser ←WebSocket→ FastAPI (main.py)
                        │
                        ├── ConversationOrchestrator
                        │       ├── ASRProvider (faster-whisper / mock)
                        │       ├── LLMProvider (llamacpp / mock)
                        │       ├── TTSProvider (pocket-tts / kokoro-onnx / mock)
                        │       └── VADProvider (silero / mock)
                        │
                        ├── RuntimeSettings (singleton, persisted)
                        ├── TTSRuntimeManager (preset selection)
                        ├── MemoryStore (companion long-term memory)
                        └── HarnessConfig (profile JSON)

Event flow:
  mic → audio.frame → VAD → speech_end → ASR → transcript →
  LLM stream_response() → tokens buffered → sentence chunks →
  TTS stream_audio() → base64 audio → tts.audio events → browser playback
```

## MCP Integration Points

### Where tool calls would be injected:
1. **`LlamaCppProvider.stream_response()`** — Must parse `delta.tool_calls` from SSE in addition to `delta.content`. Currently ignores everything except content.
2. **`orchestrator._stream_llm_and_tts()`** — When LLM emits a tool_call instead of content, the orchestrator needs to intercept, execute the tool, feed results back, and continue the LLM loop.
3. **`LLMProvider` protocol** — `stream_response()` return type must change or a new method must be added to carry structured tool_call data alongside text tokens.
4. **Profile JSON** — New `mcp` section alongside `vad`, `asr`, `llm`, `tts` to configure MCP servers (stdio/SSE transports, tool definitions).
5. **`ProviderBundle`** — Could gain an `mcp` field, or MCP could be a separate subsystem managed at the `main.py` level.
6. **`main.py` WebSocket handler** — New inbound event types like `tool.call_result` for user confirmation, new outbound events like `llm.tool_call`, `tool.executing`, `tool.result`.

### Constraints:
- **llama.cpp server supports tool calling** via OpenAI-compatible `/v1/chat/completions` with `tools` parameter — the API surface exists on the server side, the harness just doesn't use it yet.
- The `stream_response()` signature returns `AsyncIterator[str]` — tool calls need structured data, so either the return type changes or a parallel stream mechanism is needed.
- The orchestrator assumes every LLM response is text for TTS. Tool calls break that assumption — the loop needs a "text OR tool_call" discriminator.
- `messages` are `list[dict[str, str]]` — tool results need `dict[str, Any]` (content can be structured, plus `tool_call_id`, `name` fields).
- The frontend only renders text. Tool call UI would need new components.
- `httpx` is already available for MCP SSE transport. For stdio MCP servers, `subprocess` + JSON-RPC would be needed.

## Start Here

Open `app/conversational_harness/providers/llamacpp.py` — this is the LLM provider that needs tool-call parsing. Then `app/conversational_harness/orchestrator.py` for the loop that needs tool-call interception. These two files define the integration surface for MCP tool calling.
