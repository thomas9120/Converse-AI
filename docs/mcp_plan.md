# MCP Integration Plan

## Goal

Add Model Context Protocol (MCP) tool-calling support to Converse-AI so companion mode can execute assistant tasks (weather, news, calendar, web search, home automation) via MCP servers, using llama.cpp's native tool calling.

---

## Architecture Overview

```
User speech → VAD → ASR → Orchestrator
                            │
                            ▼
                     LLM (with tools array from MCP discovery)
                            │
                    ┌───────┴────────┐
                    │                │
              Text response    Tool call (finish_reason: "tool")
                    │                │
                    ▼                ▼
               TTS → playback  MCPClient.call_tool(name, args)
                                     │
                                     ▼
                              Feed result back to LLM as tool message
                                     │
                                     ▼
                              LLM generates text response → TTS → playback
```

**Transport:** stdio (subprocess) for all local MCP servers. Lowest latency (~0.1-1ms/message), no network stack.

**Key constraint:** Tool calls add ~250-1200ms latency per call. Filler messages ("Let me check that...") bridge the gap for slow tools.

---

## Phase 1 — MCP Client Layer

### 1.1 Add MCP dependency
- [ ] Add `mcp>=1.27` to `requirements.txt`

### 1.2 Create profile config schema for MCP
- [ ] Add `mcp` section to profile JSON schema. Example:
  ```json
  {
    "mcp": {
      "enabled": true,
      "servers": [
        {
          "name": "weather",
          "transport": "stdio",
          "command": "uvx",
          "args": ["open-meteo-mcp"],
          "env": {}
        }
      ],
      "tool_timeout_ms": 5000,
      "max_tool_result_chars": 2000,
      "enabled_tools": null
    }
  }
  ```
- [ ] Update `config.py` to parse `mcp` section via `config.section("mcp")`
- [ ] Add `"mcp"` to `DEFAULT_PROFILE` and create a sample profile with one MCP server

### 1.3 Create MCPClient provider
- [ ] Create `app/conversational_harness/providers/mcp_client.py`
- [ ] Implement `MCPClient` class:
  - `__init__(config: dict)` — parse server configs
  - `async start()` — connect to all configured MCP servers via stdio, run `tools/list`, cache tool schemas
  - `async stop()` — gracefully close all sessions
  - `get_openai_tools() -> list[dict]` — convert cached MCP tool schemas to OpenAI function calling format for llama.cpp
  - `async call_tool(name: str, arguments: dict) -> str` — find the right server, call `tools/call`, return result text (truncated to `max_tool_result_chars`)
  - `async check_status() -> ProviderStatus` — verify all servers are connected
  - `tool_count -> int` — number of discovered tools
- [ ] Each server connection is a `ClientSession` from the `mcp` SDK, managed via `AsyncExitStack`
- [ ] Handle server startup failures gracefully — log warning, mark server as unavailable, continue with remaining servers

### 1.4 Wire MCPClient into app lifecycle
- [ ] Add `mcp_client: MCPClient | None` field alongside the `ProviderBundle` in `main.py`
- [ ] Build and start MCPClient during app startup (after providers, before WebSocket listener)
- [ ] Stop MCPClient during app shutdown
- [ ] Expose MCP status via `/api/status` endpoint
- [ ] Add `mcp.status` WebSocket event for frontend

---

## Phase 2 — Tool Calling Bridge (llama.cpp ↔ MCP)

### 2.1 Extend LLMProvider protocol for tool calling
- [ ] Add `ToolCall` dataclass to `providers/base.py`:
  ```python
  @dataclass
  class ToolCall:
      id: str
      name: str
      arguments: str  # JSON string

  @dataclass
  class LLMToken:
      text: str | None = None
      tool_call: ToolCall | None = None
      finish_reason: str | None = None
  ```
- [ ] Add new method to `LLMProvider` protocol:
  ```python
  async def stream_response_with_tools(
      self,
      messages: list[dict],
      tools: list[dict] | None = None,
  ) -> AsyncIterator[LLMToken]: ...
  ```
- [ ] Keep existing `stream_response()` unchanged for backward compatibility
- [ ] Default implementation on `LLMProvider` protocol calls `stream_response_with_tools(tools=None)` and yields only text tokens

### 2.2 Implement tool-call parsing in LlamaCppProvider
- [ ] Modify `stream_response_with_tools()` in `providers/llamacpp.py`:
  - Accept `tools` parameter
  - When `tools` is non-empty, include `"tools": tools` and `"tool_choice": "auto"` in the `/v1/chat/completions` payload
  - Parse `delta.tool_calls` from SSE chunks (in addition to `delta.content`)
  - Parse `finish_reason: "tool"` to detect tool call completion
  - Yield `LLMToken` objects instead of raw strings
- [ ] Handle streaming tool calls: accumulate `tool_calls` deltas across chunks (function name and arguments may arrive in pieces)
- [ ] Validate that llama-server was started with `--jinja` flag (check `/props` endpoint for `chat_template_tool_use`)
- [ ] Log a clear error if `--jinja` is missing when tools are requested

### 2.3 Widen message format
- [ ] Change message type hints from `list[dict[str, str]]` to `list[dict[str, Any]]` in:
  - `LLMProvider` protocol
  - `LlamaCppProvider`
  - `ConversationOrchestrator`
- [ ] Support `role: "tool"` messages with `tool_call_id` and `content` fields per OpenAI format

### 2.4 Test tool calling bridge in isolation
- [ ] Create `tests/test_llm_tool_call.py`:
  - Mock llama.cpp SSE responses with `delta.tool_calls`
  - Verify `stream_response_with_tools()` correctly accumulates tool calls
  - Verify tool schemas are forwarded to the API payload
  - Verify `finish_reason: "tool"` detection
- [ ] Create `tests/test_mcp_client.py`:
  - Mock MCP server (FastMCP with one tool)
  - Verify `start()` discovers tools
  - Verify `get_openai_tools()` produces correct schema format
  - Verify `call_tool()` executes and returns results

---

## Phase 3 — Orchestrator Integration

### 3.1 Add tool-call loop to orchestrator
- [ ] Modify `_stream_llm_and_tts()` in `orchestrator.py`:
  - When MCP tools are available, call `stream_response_with_tools()` instead of `stream_response()`
  - On `LLMToken.tool_call`: pause TTS, execute tool via MCPClient, feed result back
  - On `LLMToken.text`: continue existing sentence-buffer → TTS pipeline
  - On `finish_reason: "tool"`: collect pending tool calls, execute, re-prompt
  - On `finish_reason: "stop"`: finalize response as normal
- [ ] Implement tool-call → re-prompt loop:
  ```
  while True:
      stream = llm.stream_response_with_tools(messages, tools)
      async for token in stream:
          if token.tool_call:
              tool_calls.append(token.tool_call)
          elif token.text:
              yield text to TTS
          if token.finish_reason == "tool":
              for tc in tool_calls:
                  result = await mcp_client.call_tool(tc.name, json.loads(tc.arguments))
                  messages.append({"role": "assistant", "tool_calls": [tc]})
                  messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})
              tool_calls.clear()
              break  # re-enter loop with updated messages
          if token.finish_reason == "stop":
              return
  ```
- [ ] Add `max_tool_rounds` config (default: 3) to prevent infinite tool-call loops
- [ ] Add `tool_timeout_ms` config — cancel tool calls that exceed the timeout

### 3.2 Filler messages for slow tools
- [ ] When a tool call is detected, immediately emit a brief filler TTS phrase:
  - Fast tools (<1s estimated): no filler
  - Slow tools (API calls): "Let me check that...", "Looking that up...", "One moment..."
- [ ] Filler text configured in profile under `mcp.filler_phrases` with defaults
- [ ] Filler TTS plays while tool executes; when tool result arrives and LLM generates final response, normal TTS takes over

### 3.3 Add WebSocket events for tool calls
- [ ] New outbound events:
  - `llm.tool_call` — `{name, arguments, call_id}` — when LLM decides to call a tool
  - `tool.executing` — `{name, status: "started"}` — tool execution begins
  - `tool.result` — `{name, result_preview, duration_ms}` — tool execution complete
  - `tool.error` — `{name, error}` — tool execution failed
  - `llm.tool_round` — `{round, max_rounds}` — tracking multi-round tool calls
- [ ] Frontend should show a small tool-call indicator in the chat UI (not critical for V1, but events should exist)

### 3.4 Error handling
- [ ] If MCPClient is not started or no tools discovered: skip tool calling, use plain `stream_response()`
- [ ] If a tool call fails (server error, timeout, invalid args): inject error message as tool result, let LLM explain the failure conversationally
- [ ] If llama.cpp doesn't support tool calling (no `--jinja`): log warning, disable tool calling, continue as plain chat

### 3.5 Barge-in during tool execution
- [ ] When user barge-in occurs during tool execution:
  - Cancel pending tool call (cancel the asyncio task)
  - Discard tool result if it arrives late
  - Cancel pending LLM re-evaluation
  - Start new turn from user speech as normal
- [ ] This extends existing barge-in logic in `handle_audio_turn()`

---

## Phase 4 — Voice UX and Companion Mode

### 4.1 Tool result truncation for voice
- [ ] Truncate tool results to `max_tool_result_chars` (default: 2000) before feeding to LLM
- [ ] Add system prompt instruction: "When presenting tool results, keep responses concise and suitable for voice output. 1-2 sentences maximum."
- [ ] For large results (calendar events, search results), instruct LLM to summarize rather than enumerate

### 4.2 Companion mode tool scoping
- [ ] In companion mode, scope available tools per-query:
  - Analyze transcript for intent (weather vs calendar vs search)
  - Only include relevant tools in the LLM request to reduce confusion for small models
  - Fallback: include all tools if intent unclear (let LLM decide)
- [ ] Or simpler V1 approach: always include all tools, rely on LLM to select correctly
  - Note: 7B models handle 3-5 tools well, degrade with 10+. Keep enabled tool count low in profiles.

### 4.3 Confirmation tier system
- [ ] Define tool permission tiers in profile config:
  ```json
  {
    "mcp": {
      "tool_permissions": {
        "auto": ["get_weather", "get_time", "search_web", "calculate"],
        "confirm": ["create_event", "send_message", "set_timer"],
        "deny": ["delete_file", "execute_shell", "write_filesystem"]
      }
    }
  }
  ```
- [ ] `auto` tools execute immediately
- [ ] `confirm` tools emit a `tool.confirm_request` WebSocket event and wait for user voice/app confirmation
- [ ] `deny` tools are excluded from the tools array sent to LLM (invisible)
- [ ] V1: implement `auto` and `deny` only. `confirm` can wait for V2 (requires UI changes)

### 4.4 System prompt integration
- [ ] When MCP tools are active, append tool-aware instructions to system prompt:
  ```
  You have access to tools for checking weather, searching the web, and managing calendar events.
  When the user asks about something a tool can help with, use it instead of guessing.
  Keep responses concise — they will be spoken aloud.
  ```
- [ ] Integrate into `RuntimeSettings.effective_system_prompt()` layering

---

## Phase 5 — Frontend Updates

### 5.1 Tool call indicator in chat UI
- [ ] Add small inline indicator in assistant message bubble when tool calls occur:
  - Show tool name + loading spinner during execution
  - Show tool name + checkmark after completion
  - Show tool name + error icon on failure
- [ ] Render tool events from WebSocket (`llm.tool_call`, `tool.executing`, `tool.result`, `tool.error`)

### 5.2 MCP status in settings panel
- [ ] Show connected MCP servers and discovered tools in the Settings/Status panel
- [ ] Show tool call count and average latency in the metrics area

### 5.3 Tool confirmation dialog (V2)
- [ ] Modal or inline confirmation for `confirm`-tier tools
- [ ] Voice confirmation: "Should I go ahead and create that calendar event?"

---

## Phase 6 — Testing and Validation

### 6.1 Unit tests
- [ ] `tests/test_mcp_client.py` — MCP client lifecycle, tool discovery, tool execution
- [ ] `tests/test_llm_tool_call.py` — Tool call parsing from SSE, tool call accumulation
- [ ] `tests/test_orchestrator_tools.py` — Tool-call loop in orchestrator, multi-round calls, error recovery

### 6.2 Integration tests
- [ ] Test with real MCP weather server (stdio): user asks "What's the weather?" → tool call → spoken response
- [ ] Test tool call failure: server returns error → LLM explains gracefully
- [ ] Test barge-in during tool execution: cancel works, no stale results leak
- [ ] Test multi-round tool calls: LLM calls tool A, then tool B based on A's result
- [ ] Test with no MCP servers configured: falls back to plain chat cleanly
- [ ] Test with llama.cpp not started with `--jinja`: graceful degradation

### 6.3 Latency benchmarks
- [ ] Measure end-to-end latency for:
  - Plain LLM response (baseline)
  - Single tool call (weather)
  - Tool call with slow API (web search)
  - Multi-round tool call
- [ ] Verify filler message plays within 500ms of tool call detection
- [ ] Target: tool-call overhead <1.5s for local tools, <3s for API tools (excluding network latency)

### 6.4 Model quality tests
- [ ] Test tool selection accuracy with Qwen 2.5 7B Q4_K_M:
  - 3 tools: expected accuracy >95%
  - 5 tools: expected accuracy >85%
  - 7+ tools: measure and document
- [ ] Test with Hermes 3 8B as alternative
- [ ] Document which models work reliably for tool calling in the README

---

## Phase 7 — Documentation and Polish

### 7.1 User-facing docs
- [ ] Add MCP configuration section to README
- [ ] Document supported MCP servers and how to configure each
- [ ] Document llama.cpp `--jinja` requirement
- [ ] Document tool permission tiers
- [ ] Document profile `mcp` section schema

### 7.2 Developer docs
- [ ] Add architecture diagram for tool-call flow to `docs/architecture.md`
- [ ] Document MCPClient provider interface
- [ ] Document new WebSocket event types
- [ ] Document `LLMToken` / `ToolCall` data types

### 7.3 Sample profiles
- [ ] Create `profiles/llamacpp-cuda-asr-mcp.json` with weather + search tools
- [ ] Create `profiles/llamacpp-local-mcp.json` with full local stack + tools
- [ ] Create `profiles/companion-full.json` with weather + calendar + search + home automation

---

## File Change Summary

| File | Change |
|------|--------|
| `requirements.txt` | Add `mcp>=1.27` |
| `providers/mcp_client.py` | **NEW** — MCP client provider |
| `providers/base.py` | Add `ToolCall`, `LLMToken` dataclasses; add `stream_response_with_tools()` to protocol |
| `providers/llamacpp.py` | Implement tool-call parsing in `stream_response_with_tools()` |
| `providers/factory.py` | Add `build_mcp()` factory function |
| `orchestrator.py` | Add tool-call loop to `_stream_llm_and_tts()` |
| `main.py` | Wire MCPClient into app lifecycle; add `/api/status` MCP info; new WebSocket events |
| `config.py` | Parse `mcp` profile section |
| `runtime_settings.py` | Add tool-aware system prompt instructions |
| `events.py` | No change (events are free-form strings) |
| `app.js` | Render tool-call indicators; show MCP status |
| `profiles/*.json` | Add `mcp` sections |
| `docs/architecture.md` | Add MCP flow diagram |

---

## Recommended Model for Tool Calling

| Model | Size | Tool Calling | Notes |
|-------|------|-------------|-------|
| **Qwen 2.5 7B Instruct** | 7B | Excellent via Hermes format | Best quality/size ratio |
| **Hermes 3 Llama 3.1 8B** | 8B | Excellent via Hermes format | Strong alternative |
| **Llama 3.1 8B Instruct** | 8B | Good via native Llama 3.x format | Well-tested |
| **Functionary Small v3.2** | 7B | Purpose-built for function calling | Specialized |

All require llama-server started with `--jinja` flag.

---

## Dependencies

```
mcp>=1.27        # MCP Python SDK (async, stdio + Streamable HTTP transports)
```

No other new dependencies. `httpx` already present for HTTP transport fallback.

---

## Risks and Open Questions

1. **Small model tool selection accuracy** — 7B models may struggle with >5 tools. Mitigate with tool scoping per query.
2. **Windows stdio compatibility** — Most MCP servers tested on macOS/Linux. Test early on Windows.
3. **Latency for voice** — Tool calls add meaningful latency. Filler messages are essential. Measure before optimizing.
4. **Barge-in race conditions** — Cancelling in-flight tool calls + pending LLM requests requires careful async cleanup.
5. **Security surface** — LLM has agency to call tools. Permission tiers mitigate but don't eliminate risk. Start with read-only tools.
6. **Prompt injection via tool results** — Malicious MCP server could inject instructions via tool output. Only connect trusted servers.
7. **Streaming tool calls** — llama.cpp streams tool call tokens; need to accumulate deltas before executing. Edge cases around partial JSON arguments.

---

## Review & Architecture Assessment

**Plan quality:** Solid. Well-structured, phased approach. Each layer isolated. Provider pattern correct. Risk analysis thorough.

**Current codebase size:**
- orchestrator.py: ~416 lines
- main.py: ~710 lines
- Total voice pipeline: ~1,500 lines

**What this plan adds:**
- MCPClient provider: ~250 lines (isolated, follows existing provider pattern)
- Tool-call loop in orchestrator: ~150 lines (necessary complexity)
- LLMProvider protocol extensions: ~50 lines
- WebSocket events + filler messages: ~100 lines
- **Total: ~550 lines**

**Bloat assessment:** Manageable. MCPClient follows existing provider pattern (like `SileroVADProvider`, `FasterWhisperProvider`, etc). Tool-call loop is core feature, not bloat.

### Separate App vs Integrated

**Separate app architecture would look like:**
```
Voice App ←WebSocket→ MCP Orchestrator
   ↓                      ↓
VAD→ASR→LLM→TTS     Tool discovery/execution
                         ↓
                    MCP servers (stdio)
```

**Separate app pros:**
- Voice app stays lean (~1,500 lines forever)
- MCP service reusable across multiple clients
- Independent evolution/deployment
- Clear boundary: voice vs tool execution

**Separate app cons:**
- Network latency (even localhost: ~1-5ms per message)
- Barge-in coordination complex (cancel signal must cross process boundary)
- Filler message timing critical — needs tight orchestrator coupling
- Shared message history sync
- More deployment complexity (2 services vs 1)
- Debugging harder (logs split across apps)

**Critical issue:** Tool-call loop requires real-time coordination:
1. Detect `finish_reason: "tool"` → pause TTS
2. Emit filler phrase → play via TTS
3. Execute tool → wait for result
4. Inject result → resume LLM stream

This needs sub-100ms coordination. Separate app adds latency at every step.

### Recommendation

**V1 (now):** Keep integrated. Plan is solid. ~550 lines added is reasonable for a core feature. MCPClient as provider is clean architecture.

**V2 (if needed):** Extract MCP orchestration to separate service IF:
- You build a second client (mobile app, web-only client)
- MCP tools become complex (multi-step workflows, state machines)
- You want MCP to persist across voice app restarts
- Tool execution becomes slow enough that async background processing helps

**Refactoring path:** Keep MCPClient provider interface stable. Tool-call loop can move to separate service later via WebSocket bridge. Voice app sends `tool.execute_request`, receives `tool.execute_result`. MCPClient already isolated behind async interface — extraction is straightforward when the time comes.

**Verdict:** Plan good. Bloat acceptable. Separate app = premature optimization for single-client use case. Ship integrated, refactor if multi-client need emerges.
