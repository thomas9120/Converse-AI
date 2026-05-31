# Research: MCP Tool Calling for Local Voice Assistant (llama.cpp)

## Summary

llama.cpp server **natively supports** OpenAI-style `tools` and `tool_choice` parameters on `/v1/chat/completions` via the `--jinja` flag, with native handlers for Llama 3.x, Qwen 2.5, Hermes, Mistral Nemo, and others — plus a generic fallback for all models. The official `mcp` Python SDK (v1.27.2, 23K+ GitHub stars) is mature, fully async, and supports stdio and Streamable HTTP transports. For a local desktop voice assistant, **stdio transport** is the lowest-latency and simplest choice. Existing projects (Hachimi, mcp-use-voice-assistant, fastrtc-mcpo) demonstrate the full voice+MCP pattern but all use cloud LLMs — none target llama.cpp specifically.

---

## 1. llama.cpp Tool Calling Support

### Findings

1. **Native `tools` and `tool_choice` support is live.** The llama.cpp server (`llama-server`) supports OpenAI-style function calling on `/v1/chat/completions` when launched with the `--jinja` flag. You pass `tools` (array of function definitions) and `tool_choice` in the request body exactly like the OpenAI API. The response returns `finish_reason: "tool"` with `tool_calls` array. [Source](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)

2. **Native format handlers exist for popular model families:**
   - Llama 3.1/3.2/3.3 (including built-in tools: wolfram_alpha, web_search, code_interpreter)
   - Functionary v3.1/v3.2
   - Hermes 2/3, Qwen 2.5, Qwen 2.5 Coder
   - Mistral Nemo
   - Firefunction v2
   - Command R7B
   - DeepSeek R1 (WIP)
   
   These use model-specific token formats for maximum efficiency. [Source](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)

3. **Generic fallback works for ALL models.** When the model's Jinja template doesn't match a known native handler, the server falls back to a generic tool call format. This consumes more tokens but works with any model. You can also force `--chat-template chatml` as a universal fallback. [Source](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)

4. **`parallel_tool_calls` supported** but disabled by default. Pass `"parallel_tool_calls": true` in the payload. Only some models support it. [Source](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)

5. **`parse_tool_calls` parameter** available on `/v1/chat/completions` to control whether generated tool calls are parsed from output. [Source](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

6. **Built-in server tools** are also available via `--tools all` (file read/write, grep, exec shell command, etc.) — but these are designed for the web UI, not for API consumption. The README explicitly says "do NOT use this endpoint in downstream applications." [Source](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

7. **Anthropic-compatible `/v1/messages` also supports tools** with `--jinja` flag, including `tool_choice` modes (`auto`, `any`, `tool`). [Source](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)

### Practical Implementation

```bash
# Start with tool calling enabled
llama-server --jinja -fa -hf bartowski/Qwen2.5-7B-Instruct-GGUF:Q4_K_M
```

```python
# Python request with tools
import httpx

resp = await httpx.AsyncClient().post("http://localhost:8080/v1/chat/completions", json={
    "model": "gpt-3.5-turbo",
    "messages": [{"role": "user", "content": "What's the weather in Tokyo?"}],
    "tools": [{
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}},
                "required": ["location"]
            }
        }
    }]
})
# Response: finish_reason="tool", tool_calls=[{"name":"get_weather","arguments":"{\"location\":\"Tokyo\"}"}]
```

**Confidence: HIGH** — Directly from llama.cpp official docs and README. Feature is live in current master.

---

## 2. Python MCP Client Libraries

### Findings

1. **Official `mcp` Python SDK (v1.27.2)** — The canonical library by Anthropic/MCP team. 23K+ GitHub stars, 190+ contributors, actively maintained (last push May 30, 2026). Licensed MIT. [Source](https://github.com/modelcontextprotocol/python-sdk)

2. **Full async support.** The SDK uses `anyio` for async I/O and is fully compatible with asyncio/uvicorn. Both client and server APIs are async-first. Supports stdio and Streamable HTTP transports natively. [Source](https://pypi.org/project/mcp/)

3. **Client usage pattern:**
   ```python
   from mcp import ClientSession, StdioServerParameters
   from mcp.client.stdio import stdio_client
   
   server_params = StdioServerParameters(command="python", args=["my_server.py"])
   async with stdio_client(server_params) as (read, write):
       async with ClientSession(read, write) as session:
           await session.initialize()
           tools = await session.list_tools()
           result = await session.call_tool("get_weather", {"location": "Tokyo"})
   ```
   [Source](https://github.com/modelcontextprotocol/python-sdk)

4. **Third-party alternatives exist but are less mature:**
   - **mcpwire** (`anukchat/mcpwire`) — wraps official SDK + langchain-mcp-adapters, adds resource support and prompt sending. [Source](https://github.com/anukchat/mcpwire)
   - **dedalus-mcp-python** (`dedalus-labs/dedalus-mcp-python`) — minimal, spec-faithful framework with ergonomic decorators. Explicitly positions itself against FastMCP for users who want less opinionation. [Source](https://github.com/dedalus-labs/dedalus-mcp-python)
   - **mcp-use** (`mcp-use/mcp-use`) — higher-level agent framework that orchestrates MCP tool calling with LLMs. Powers the mcp-use-voice-assistant project. [Source](https://github.com/mcp-use/mcp-use-voice-assistant)
   - **ollama-mcpo-adapter** — converts MCP tool schemas to OpenAI function calling format, bridges MCP to Ollama. Used in the fastrtc-mcpo-voice-assistant project. [Source](https://github.com/dwain-barnes/fastrtc-mcpo-voice-assistant)

5. **LangChain integration** available via `langchain-mcp-adapters`, enabling MCP tools to be used as LangChain tools seamlessly.

### Recommendation for This Project

Use the **official `mcp` SDK** directly. It's async-native, well-documented, actively maintained, and supports all transports. No need for third-party wrappers. For llama.cpp tool calling integration, write a thin adapter that:
1. Discovers MCP tools via `session.list_tools()`
2. Converts MCP tool schemas to OpenAI function format for llama.cpp
3. Routes `tool_calls` from llama.cpp responses back to `session.call_tool()`

**Confidence: HIGH** — Official SDK is the clear choice. Well-established ecosystem.

---

## 3. Transport Considerations

### MCP Transport Options (Spec 2025-06-18)

| Transport | Mechanism | Latency | Complexity | Multi-client |
|-----------|-----------|---------|------------|--------------|
| **stdio** | Subprocess stdin/stdout, newline-delimited JSON-RPC | **Lowest** (no network stack) | Simplest | No (1:1) |
| **Streamable HTTP** | HTTP POST + optional SSE for server-to-client | Low (localhost HTTP) | Moderate | Yes |
| ~~HTTP+SSE~~ | Deprecated, replaced by Streamable HTTP | — | — | — |

### Findings

1. **stdio is the recommended default for local desktop.** The MCP spec says "Clients SHOULD support stdio whenever possible." Zero network overhead, no port conflicts, no DNS rebinding attacks. The client launches the MCP server as a subprocess. [Source](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)

2. **Streamable HTTP is the production network transport.** Supersedes the old SSE transport. Server runs as independent process, handles multiple clients via session management (`Mcp-Session-Id` header). Supports SSE streaming for server-to-client notifications. [Source](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)

3. **No WebSocket transport in the official spec.** WebSocket is not a standard MCP transport. Custom transports are allowed but not standardized.

4. **Latency comparison for local use:**
   - **stdio**: ~0.1-1ms per message (subprocess pipe, no TCP handshake)
   - **Streamable HTTP localhost**: ~1-5ms per message (TCP + HTTP overhead)
   - **Streamable HTTP remote**: Variable, depends on network
   
   For real-time voice where tool calls add to end-to-end latency, stdio is the clear winner.

5. **For this harness:** Use stdio for all local MCP servers (filesystem, calendar, etc.). Use Streamable HTTP only if connecting to remote MCP servers (e.g., home automation on another machine). The `mcp` Python SDK supports both via simple config.

**Confidence: HIGH** — Transport specs are clear. Latency analysis is straightforward from the architecture.

---

## 4. Latency Impact of MCP Tool Calling

### End-to-End Latency Breakdown

For a voice conversation with tool calling, the pipeline becomes:

```
ASR → LLM (decides tool) → MCP tool discovery → MCP tool execution → LLM (generates response) → TTS
```

### Findings

1. **Tool discovery is a one-time cost.** `tools/list` is called once at startup (or on `notifications/tools/list_changed`). It returns all available tools in a single JSON-RPC response. Cost: ~1-5ms for stdio, cached for the session.

2. **Per-tool-call overhead:**
   - LLM generates tool call tokens: adds N tokens to generation (~50-200ms depending on model speed)
   - MCP `tools/call` request/response: ~1-10ms for stdio (JSON-RPC over pipe)
   - Tool execution itself: varies wildly (filesystem read = <1ms, API call = 100-5000ms)
   - LLM re-processes tool result as new message: prompt evaluation + generation (~200-1000ms)

3. **Total tool-call overhead (excluding tool execution itself): ~250-1200ms**
   - LLM token generation for tool call: ~50-200ms
   - MCP round-trip: ~1-10ms
   - LLM re-evaluation with tool result: ~200-1000ms

4. **Compared to base voice pipeline latency (no tools):** Tool calls add roughly one additional LLM round-trip. If the base pipeline is ~1-3 seconds (VAD → ASR → LLM first token → TTS first chunk), a tool call roughly doubles the response time.

5. **Mitigations for voice UX:**
   - Stream LLM tokens so "Let me check..." can start TTS before the tool call completes
   - Pre-filter tools so the LLM has fewer to choose from (reduces decision latency)
   - Use small, fast tools for voice interactions
   - Cache tool results where possible

**Confidence: MODERATE** — Latency estimates are based on architecture analysis, not measured benchmarks. Actual numbers depend heavily on model size, quantization, GPU speed, and tool execution time.

---

## 5. Tool Calling Flow for Voice Conversations

### Recommended Flow

```
User speaks → VAD detects end → ASR transcribes → Orchestrator sends to LLM with tools
                                                              │
                                                    ┌─────────┴─────────┐
                                                    │ LLM generates:    │
                                                    │  A) Text response │
                                                    │  B) Tool call     │
                                                    └─────────┬─────────┘
                                                              │
                                            ┌─────────────────┼─────────────────┐
                                            │                 │                  │
                                     Text response    Tool call           Tool call
                                            │          (fast tool)        (slow tool)
                                            │                 │                  │
                                     TTS → playback   Execute tool    "Let me check..."
                                            │                 │           TTS → playback
                                            ▼                 ▼                  │
                                       User hears       Feed result to    Execute tool
                                       response          LLM → generate    async
                                                             │                │
                                                        TTS → playback    Feed result → LLM
                                                             │                │
                                                        User hears       TTS → playback
                                                        response
```

### UX Considerations

1. **Filler message for slow tools.** When the LLM emits a tool call for something that might take >1s (API call, web search), the orchestrator should immediately generate a brief filler ("Let me look that up...", "Checking now...") and start TTS playback while the tool executes in parallel. This prevents awkward silence.

2. **No filler for fast tools.** For local operations (calculator, file read, time query), skip the filler. The round-trip is fast enough that silence is preferable to a redundant announcement.

3. **Multi-turn tool calls.** The LLM may call multiple tools in sequence. The orchestrator should:
   - Execute tool calls sequentially (or parallel if `parallel_tool_calls` enabled)
   - Accumulate results
   - Feed all results back to the LLM in a single follow-up message
   - Only start TTS when the LLM generates a final text response (not more tool calls)

4. **Tool result size management.** MCP tool results can be large (e.g., file contents). For voice, truncate or summarize tool results before feeding to LLM. The LLM should generate a voice-appropriate summary, not read raw data.

5. **Tool call streaming.** llama.cpp streams tool call tokens. The orchestrator can detect `finish_reason: "tool"` early and begin tool execution before the full response completes, reducing latency.

6. **Barge-in during tool execution.** If the user interrupts while a tool is executing, cancel the tool call and pending LLM request. Discard the tool result. This is consistent with the existing barge-in architecture in the harness.

**Confidence: HIGH** — Based on standard OpenAI tool calling patterns adapted for voice UX. Several existing voice+MCP projects use similar flows.

---

## 6. Security and Safety Considerations

### Findings

1. **MCP spec has no built-in permission model for tool execution.** The protocol defines tool discovery and invocation but leaves authorization to the transport layer (OAuth 2.1 for HTTP) or environment-level controls (stdio). There is no "ask the user before calling this tool" in the protocol itself. [Source](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)

2. **HTTP transport supports OAuth 2.1.** For Streamable HTTP, the spec defines a full OAuth 2.1 flow with dynamic client registration, PKCE, and token audience binding. This is relevant for remote MCP servers but overkill for local stdio. [Source](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)

3. **Security responsibilities split between client and server:**
   - **Servers MUST:** Validate all tool inputs, implement access controls, rate limit invocations, sanitize outputs
   - **Clients SHOULD:** Prompt for user confirmation on sensitive operations, show tool inputs before calling, validate results, implement timeouts, log usage for audit
   [Source](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)

4. **For a local voice assistant, the critical risks are:**
   - **Prompt injection via tool results.** A malicious tool (or compromised MCP server) could return data designed to manipulate the LLM into taking harmful actions. Mitigate by treating tool results as untrusted input.
   - **Unintended tool execution.** The LLM may call tools the user didn't intend. Implement a confirmation layer for dangerous tools (file deletion, shell execution, financial transactions).
   - **Tool result exfiltration.** A malicious MCP server could exfiltrate data from tool call arguments. Only connect to trusted MCP servers.
   - **Subprocess management.** stdio transport means launching subprocesses. Validate MCP server binaries/commands before execution.

5. **Recommended permission tiers for voice assistant:**
   - **Auto-execute (no confirmation):** Read-only tools (weather, time, calculator, search)
   - **Silent execute with logging:** Write tools with low risk (set timer, add note)
   - **Require confirmation:** Destructive tools (delete file, send message, make purchase)
   - **Disabled by default:** Shell execution, filesystem write, network access

6. **Tool annotations.** MCP tools support `annotations` with `audience` and `priority` fields. Use these to mark which tools are safe for automated execution vs. need user confirmation.

**Confidence: HIGH** — Based on MCP specification security section and general LLM security best practices.

---

## 7. Existing Voice + MCP Projects

### Findings

1. **Hachimi** (`cyijun/hachimi`) — **Most relevant reference.** Modular multiprocessing voice assistant with full MCP integration. Features: wake word detection (openwakeword), VAD (webrtcvad), barge-in support, vector-based tool selection (BGE-M3 embeddings), context management with summarization, multiple MCP servers (SSE + stdio). Uses cloud APIs (DeepSeek for LLM, SiliconFlow for STT/TTS). **Architecture is very close to this harness** but uses cloud instead of local models. [Source](https://github.com/cyijun/hachimi)

2. **mcp-use-voice-assistant** (`mcp-use/mcp-use-voice-assistant`) — Uses `mcp-use` library + LangChain for MCP tool orchestration. Voice via Whisper (STT) + ElevenLabs (TTS). Works with any LLM provider supporting tool calling (OpenAI, Anthropic, Groq). Simple architecture, good reference for MCP client integration patterns. [Source](https://github.com/mcp-use/mcp-use-voice-assistant)

3. **fastrtc-mcpo-voice-assistant** (`dwain-barnes/fastrtc-mcpo-voice-assistant`) — Uses **Ollama** (local LLM) + FastRTC for real-time voice. Converts MCP tools to OpenAI function calling format via `ollama-mcpo-adapter`. Uses Kokoro TTS and Moonshine STT. **Closest to a fully-local stack** but uses Ollama, not llama.cpp directly. [Source](https://github.com/dwain-barnes/fastrtc-mcpo-voice-assistant)

4. **VoiceMCP** (`tamirdresher/mcp-voice-assist`) — MCP server (not client) that adds voice I/O capabilities to AI agents. Uses Azure OpenAI TTS + Whisper. Different angle: makes voice a tool, not a transport. [Source](https://github.com/tamirdresher/mcp-voice-assist)

5. **voice-mcp** (`alefrometa/voice-mcp`) — MCP server providing TTS (Kokoro) and STT (Whisper) as tools. Similar to VoiceMCP but uses Kokoro TTS engine. [Source](https://github.com/alefrometa/voice-mcp)

### Key Takeaway

No existing project combines **llama.cpp server** + **local STT/TTS** + **MCP tool calling** + **real-time voice conversation**. The Hachimi architecture is the closest reference for the orchestration pattern. The fastrtc-mcpo project demonstrates local LLM + MCP + voice but via Ollama. **This harness would be the first to use llama.cpp's native tool calling with MCP in a local voice assistant.**

**Confidence: HIGH** — Based on direct GitHub repository analysis.

---

## Recommended Integration Path

### Phase 1: MCP Client Layer
1. Add `mcp` Python SDK as dependency
2. Create `MCPProvider` class (matching existing provider pattern) that:
   - Connects to MCP servers via stdio (config-driven)
   - Discovers tools via `tools/list`
   - Converts MCP tool schemas → OpenAI function format
   - Executes tool calls via `tools/call`
   - Returns results

### Phase 2: Tool Calling Bridge
3. Extend `LlamaCppProvider` to accept `tools` parameter
4. When tools are available, include them in `/v1/chat/completions` requests
5. Handle `finish_reason: "tool"` responses → route to MCPProvider
6. Feed tool results back as assistant+tool messages

### Phase 3: Voice Integration
7. Add tool calling to the orchestrator event loop
8. Implement filler message logic for slow tools
9. Add tool call events to WebSocket for UI visibility
10. Implement confirmation layer for dangerous tools

### Phase 4: Polish
11. Add tool result caching
12. Implement tool result size limits for voice
13. Add per-profile tool configuration
14. Add tool calling latency metrics

---

## Sources

### Kept
- llama.cpp function-calling docs — definitive reference for tool calling support [URL](https://github.com/ggml-org/llama.cpp/blob/master/docs/function-calling.md)
- llama.cpp server README — API endpoint documentation [URL](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)
- Official `mcp` Python SDK — canonical MCP client library [URL](https://github.com/modelcontextprotocol/python-sdk)
- MCP Tools specification — protocol definition for tool discovery/invocation [URL](https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
- MCP Transports specification — stdio and Streamable HTTP details [URL](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)
- MCP Authorization specification — OAuth 2.1 flow for HTTP transport [URL](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
- Hachimi voice assistant — most complete voice+MCP reference implementation [URL](https://github.com/cyijun/hachimi)
- mcp-use-voice-assistant — clean MCP client pattern with LangChain [URL](https://github.com/mcp-use/mcp-use-voice-assistant)
- fastrtc-mcpo-voice-assistant — local Ollama + MCP + voice, closest to target stack [URL](https://github.com/dwain-barnes/fastrtc-mcpo-voice-assistant)
- MCP client tools comparison — ecosystem overview [URL](https://github.com/kb4ai/mcp-client-tools-comparison-pub-kb)
- llama.cpp PR #9639 — original tool calling implementation [URL](https://github.com/ggml-org/llama.cpp/pull/9639)

### Dropped
- Various generic MCP tutorials (DEV Community, microsoft/mcp-for-beginners) — introductory content, no new technical detail
- dedalus-mcp-python, mcpwire — third-party SDKs, official SDK is sufficient
- VoiceMCP, voice-mcp — MCP servers for voice I/O, not relevant to client-side integration

## Gaps

1. **No measured latency benchmarks** for MCP tool calling in a voice pipeline. Estimates are architectural, not empirical. Should measure once integrated.
2. **No direct llama.cpp + MCP integration examples** exist. The tool-calling bridge (MCP schema → OpenAI function format → llama.cpp → result → MCP call_tool) must be written from scratch.
3. **Tool calling accuracy for local models** is uncertain. Qwen 2.5 7B and Hermes models are known to work, but reliability for voice-style short prompts with many tools is untested. Larger models (14B+) may be needed for reliable tool selection.
4. **Concurrent tool calls + streaming TTS** interaction is unexplored. The orchestrator must handle interleaving tool execution, filler message TTS, and final response TTS without audio glitches.
5. **MCP server ecosystem for desktop assistants** is thin. Most existing MCP servers target coding/web tasks. Home automation, media control, and system operation servers would need to be created or adapted.
