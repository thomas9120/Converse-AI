from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
import struct
from collections import deque
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from conversational_harness.audio_frames import (
    AudioFrameStats,
    compute_pcm16_level,
    parse_audio_frame,
    trim_pcm16_silence,
)
from conversational_harness.config import PROJECT_ROOT, load_config
from conversational_harness.orchestrator import ConversationOrchestrator, QueueEventSink
from conversational_harness.providers.factory import (
    build_provider_bundle,
    serialize_statuses,
)
from conversational_harness.runtime_settings import (
    MemoryStore,
    load_runtime_settings,
    parse_character_json,
    parse_character_png,
    save_runtime_settings,
)
from conversational_harness.tts_runtime import TTSRuntimeManager, load_tts_presets

logger = logging.getLogger(__name__)

STATIC_ROOT = PROJECT_ROOT / "app" / "static"
MAX_CHARACTER_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_CHARACTER_UPLOAD_BASE64_CHARS = ((MAX_CHARACTER_UPLOAD_BYTES + 2) // 3) * 4


@asynccontextmanager
async def lifespan(app: FastAPI):
    await _fetch_llm_server_defaults()
    yield
    await cancel_active_tts("shutdown")
    for queue in list(ACTIVE_QUEUES):
        ACTIVE_QUEUES.discard(queue)


app = FastAPI(title="Converse-AI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

BASE_CONFIG = load_config()
TTS_MANAGER = TTSRuntimeManager(BASE_CONFIG, load_tts_presets())
RUNTIME_SETTINGS = load_runtime_settings(BASE_CONFIG.section("llm"))
MEMORY_STORE = MemoryStore()
ACTIVE_QUEUES: set[asyncio.Queue[dict[str, Any]]] = set()
TTS_CANCEL_HOOKS: set[Any] = set()
TURN_CONFIG_HOOKS: set[Any] = set()
CHARACTER_SEED_HOOKS: set[Any] = set()
COMPANION_HISTORY_HOOKS: set[Any] = set()


async def _fetch_llm_server_defaults() -> None:
    llm_config = BASE_CONFIG.section("llm")
    base_url = str(llm_config.get("base_url", "http://127.0.0.1:8080")).rstrip("/")
    try:
        import httpx

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(connect=1.0, read=3.0)
        ) as client:
            resp = await client.get(f"{base_url}/props")
            resp.raise_for_status()
            params = (
                resp.json().get("default_generation_settings", {}).get("params", {})
            )
            RUNTIME_SETTINGS.set_server_defaults(params)
            logger.info(
                "Fetched llama.cpp server defaults: %s",
                list(RUNTIME_SETTINGS.server_defaults.keys()),
            )
    except Exception as exc:
        logger.info("Could not fetch llama.cpp server defaults: %s", exc)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    return await status_payload()


@app.post("/api/tts/select")
async def select_tts(payload: dict[str, Any]) -> dict[str, Any]:
    preset_id = str(payload.get("preset_id", "")).strip()
    if not preset_id:
        raise HTTPException(status_code=400, detail="preset_id is required")
    await cancel_active_tts("tts_select")
    try:
        runtime = await TTS_MANAGER.select_preset(preset_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await refresh_turn_config()
    await broadcast_providers_status()
    return runtime


@app.post("/api/tts/load")
async def load_tts_model() -> dict[str, Any]:
    await cancel_active_tts("tts_load")
    runtime = await TTS_MANAGER.load_selected()
    await refresh_turn_config()
    await broadcast_providers_status()
    return runtime


@app.post("/api/tts/unload")
async def unload_tts_model() -> dict[str, Any]:
    await cancel_active_tts("tts_unload")
    runtime = await TTS_MANAGER.unload_selected()
    await refresh_turn_config()
    await broadcast_providers_status()
    return runtime


@app.post("/api/tts/voice")
async def select_tts_voice(payload: dict[str, Any]) -> dict[str, Any]:
    voice_id = str(payload.get("voice_id", "")).strip()
    if not voice_id:
        raise HTTPException(status_code=400, detail="voice_id is required")
    await cancel_active_tts("tts_voice_select")
    try:
        runtime = await TTS_MANAGER.select_voice(voice_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    await refresh_turn_config()
    await broadcast_providers_status()
    return runtime


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return RUNTIME_SETTINGS.to_dict()


@app.patch("/api/settings")
async def patch_settings(payload: dict[str, Any]) -> dict[str, Any]:
    RUNTIME_SETTINGS.apply_patch(payload)
    save_runtime_settings(RUNTIME_SETTINGS)
    await broadcast_settings()
    return RUNTIME_SETTINGS.to_dict()


@app.get("/api/companion/memory")
async def get_companion_memory() -> dict[str, Any]:
    return MEMORY_STORE.payload()


@app.put("/api/companion/memory")
async def put_companion_memory(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        MEMORY_STORE.write(str(payload.get("text", "")))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return MEMORY_STORE.payload()


@app.delete("/api/companion/memory")
async def delete_companion_memory() -> dict[str, Any]:
    MEMORY_STORE.clear()
    return MEMORY_STORE.payload()


def _valid_summary_messages(raw_messages: Any) -> list[dict[str, str]]:
    if not isinstance(raw_messages, list):
        return []
    messages: list[dict[str, str]] = []
    for item in raw_messages:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages


@app.post("/api/companion/memory/summarize")
async def summarize_companion_memory(
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    messages = _valid_summary_messages((payload or {}).get("messages"))
    for hook in list(COMPANION_HISTORY_HOOKS):
        if messages:
            break
        messages = _valid_summary_messages(hook())
    if not messages:
        raise HTTPException(
            status_code=400, detail="No companion conversation to summarize"
        )

    providers = build_provider_bundle(
        BASE_CONFIG, tts_provider=TTS_MANAGER.get_provider()
    )
    if hasattr(providers.llm, "set_runtime_settings"):
        providers.llm.set_runtime_settings(RUNTIME_SETTINGS)
    RUNTIME_SETTINGS.active_mode = "companion"
    summary_prompt = [
        {
            "role": "system",
            "content": (
                "Summarize durable companion memory from this conversation. "
                "Keep stable user preferences, personal facts, relationship context, and ongoing plans. "
                "Omit transient wording, filler, and anything uncertain. Use concise Markdown bullets."
            ),
        },
        *messages,
        {"role": "user", "content": "Write the memory update now."},
    ]
    chunks: list[str] = []
    try:
        async for token in providers.llm.stream_response(summary_prompt):
            chunks.append(token)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Memory summary failed: {exc}"
        ) from exc
    title = datetime.now().strftime("%Y-%m-%d %H:%M")
    try:
        text = MEMORY_STORE.append_summary("".join(chunks), title)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"text": text, "metadata": MEMORY_STORE.metadata()}


@app.post("/api/settings/character")
async def import_character(payload: dict[str, Any]) -> dict[str, Any]:
    char_data = payload.get("character")
    if not char_data or not isinstance(char_data, dict):
        raise HTTPException(status_code=400, detail="character object is required")
    card = parse_character_json(json.dumps(char_data))
    if not card.name:
        raise HTTPException(status_code=400, detail="Character card must have a name")
    RUNTIME_SETTINGS.set_character(card)
    save_runtime_settings(RUNTIME_SETTINGS)
    await broadcast_settings()
    await seed_character_first_messages()
    return RUNTIME_SETTINGS.to_dict()


@app.post("/api/settings/character/upload")
async def upload_character(payload: dict[str, Any]) -> dict[str, Any]:
    file_data = payload.get("data")
    filename = str(payload.get("filename", "")).lower()
    if not isinstance(file_data, str) or not file_data:
        raise HTTPException(status_code=400, detail="file data is required")
    if not (filename.endswith(".png") or filename.endswith(".json")):
        raise HTTPException(
            status_code=400, detail="Unsupported file type. Use .png or .json"
        )
    # Keep character card uploads small; they are prompt metadata, not model assets.
    if len(file_data) > MAX_CHARACTER_UPLOAD_BASE64_CHARS:
        raise HTTPException(
            status_code=400, detail="Character card upload is too large"
        )
    try:
        raw = base64.b64decode(file_data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(
            status_code=400, detail="file data must be valid base64"
        ) from exc
    if len(raw) > MAX_CHARACTER_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400, detail="Character card upload is too large"
        )
    try:
        if filename.endswith(".png"):
            card = parse_character_png(raw)
        else:
            card = parse_character_json(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="Character JSON must be valid UTF-8"
        ) from exc
    except (json.JSONDecodeError, ValueError, IndexError, struct.error) as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid character card: {exc}"
        ) from exc
    if not card.name:
        raise HTTPException(status_code=400, detail="Character card must have a name")
    RUNTIME_SETTINGS.set_character(card)
    save_runtime_settings(RUNTIME_SETTINGS)
    await broadcast_settings()
    await seed_character_first_messages()
    return RUNTIME_SETTINGS.to_dict()


@app.delete("/api/settings/character")
async def delete_character() -> dict[str, Any]:
    RUNTIME_SETTINGS.clear_character()
    save_runtime_settings(RUNTIME_SETTINGS)
    await broadcast_settings()
    return RUNTIME_SETTINGS.to_dict()


def profile_summary(raw: dict[str, Any]) -> list[dict[str, Any]]:
    summary = []
    for kind in ("vad", "asr", "llm", "tts"):
        section = raw.get(kind, {})
        if not isinstance(section, dict):
            continue
        summary.append(
            {
                "kind": kind,
                "provider": section.get("provider", "mock"),
                "model": section.get("model"),
                "device": section.get("device"),
                "compute_type": section.get("compute_type"),
                "endpoint": section.get("base_url") or section.get("url"),
                "voice": section.get("voice"),
            }
        )
    return summary


async def merged_profile_raw() -> dict[str, Any]:
    return TTS_MANAGER.merged_profile_raw()


async def provider_statuses_payload() -> list[dict[str, Any]]:
    providers = build_provider_bundle(
        BASE_CONFIG, tts_provider=TTS_MANAGER.get_provider()
    )
    return await provider_statuses_for_bundle(providers)


async def provider_statuses_for_bundle(providers) -> list[dict[str, Any]]:
    tts_runtime = await TTS_MANAGER.describe()
    items = [
        await providers.vad.check_status(),
        await providers.asr.check_status(),
        await providers.llm.check_status(),
    ]
    statuses = serialize_statuses(items)
    statuses.append(tts_runtime["status"])
    return statuses


async def providers_status_event() -> dict[str, Any]:
    raw = await merged_profile_raw()
    return {
        "type": "providers.status",
        "payload": {
            "providers": await provider_statuses_payload(),
            "summary": profile_summary(raw),
            "tts_runtime": await TTS_MANAGER.describe(),
        },
    }


async def status_payload() -> dict[str, Any]:
    raw = await merged_profile_raw()
    return {
        "profile": {
            "name": BASE_CONFIG.name,
            "path": str(BASE_CONFIG.path),
            "description": BASE_CONFIG.raw.get("description", ""),
            "audio": BASE_CONFIG.section("audio"),
            "turn": raw.get("turn", {}),
            "summary": profile_summary(raw),
        },
        "providers": await provider_statuses_payload(),
        "tts_runtime": await TTS_MANAGER.describe(),
        "settings": RUNTIME_SETTINGS.to_dict(),
    }


async def broadcast(event: dict[str, Any]) -> None:
    if not ACTIVE_QUEUES:
        return
    stale = []
    for queue in ACTIVE_QUEUES:
        try:
            queue.put_nowait(event)
        except RuntimeError:
            stale.append(queue)
    for queue in stale:
        ACTIVE_QUEUES.discard(queue)


async def broadcast_providers_status() -> None:
    await broadcast(await providers_status_event())


async def broadcast_settings() -> None:
    await broadcast({"type": "settings.updated", "payload": RUNTIME_SETTINGS.to_dict()})


async def cancel_active_tts(reason: str) -> None:
    for hook in list(TTS_CANCEL_HOOKS):
        await hook(reason)


async def refresh_turn_config() -> None:
    config = TTS_MANAGER.current_turn_config()
    for hook in list(TURN_CONFIG_HOOKS):
        hook(config)


async def seed_character_first_messages() -> None:
    for hook in list(CHARACTER_SEED_HOOKS):
        await hook()


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    providers = build_provider_bundle(
        BASE_CONFIG, tts_provider=TTS_MANAGER.get_provider()
    )
    if hasattr(providers.llm, "set_runtime_settings"):
        providers.llm.set_runtime_settings(RUNTIME_SETTINGS)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    ACTIVE_QUEUES.add(queue)
    sink = QueueEventSink(queue)
    audio_config = BASE_CONFIG.section("audio")
    audio_stats = AudioFrameStats(
        expected_sample_rate=int(audio_config.get("sample_rate", 16000)),
        expected_channels=int(audio_config.get("channels", 1)),
        expected_frame_ms=int(audio_config.get("frame_ms", 30)),
    )
    vad_config = BASE_CONFIG.section("vad")
    asr_config = BASE_CONFIG.section("asr")
    min_speech_duration_ms = int(vad_config.get("min_speech_duration_ms", 0))
    reject_low_energy_rms = float(asr_config.get("reject_low_energy_rms", 0))
    reject_low_energy_max_duration_ms = int(
        asr_config.get("reject_low_energy_max_duration_ms", 0)
    )
    reject_utterance_rms = float(asr_config.get("reject_utterance_rms", 0))
    trim_silence_rms = float(asr_config.get("trim_silence_rms", 0))
    trim_silence_frame_ms = int(
        asr_config.get("trim_silence_frame_ms", audio_stats.expected_frame_ms)
    )
    pre_speech_frames = (
        int(audio_config.get("pre_speech_ms", 450)) // audio_stats.expected_frame_ms
    )
    max_utterance_frames = (
        int(audio_config.get("max_utterance_ms", 30000))
        // audio_stats.expected_frame_ms
    )
    bytes_per_ms = audio_stats.expected_sample_rate * 2 // 1000
    expected_frame_bytes = bytes_per_ms * audio_stats.expected_frame_ms
    pre_buffer: deque[bytes] = deque(maxlen=max(1, pre_speech_frames))
    utterance_buffer = bytearray()
    recording_utterance = False
    recording_mode = "chat"
    turn_config = TTS_MANAGER.current_turn_config()
    orchestrator = ConversationOrchestrator(
        providers=providers,
        sink=sink,
        tts_chunk_chars=int(turn_config.get("tts_chunk_chars", 120)),
        min_tts_chars=int(turn_config.get("min_tts_chars", 0)),
        runtime_settings=RUNTIME_SETTINGS,
        memory_store=MEMORY_STORE,
    )

    async def cancel_hook(reason: str) -> None:
        await orchestrator.cancel_tts(reason)

    async def seed_hook() -> None:
        await orchestrator.seed_character_first_message()

    def companion_history_hook() -> list[dict[str, str]]:
        return orchestrator.messages_for_mode("companion")

    def turn_config_hook(config: dict[str, Any]) -> None:
        orchestrator.update_turn_config(
            tts_chunk_chars=int(config.get("tts_chunk_chars", 120)),
            min_tts_chars=int(config.get("min_tts_chars", 0)),
        )
        orchestrator.providers.tts = TTS_MANAGER.get_provider()

    TTS_CANCEL_HOOKS.add(cancel_hook)
    TURN_CONFIG_HOOKS.add(turn_config_hook)
    CHARACTER_SEED_HOOKS.add(seed_hook)
    COMPANION_HISTORY_HOOKS.add(companion_history_hook)
    await orchestrator.seed_character_first_message()

    async def warm_asr_on_connect() -> None:
        if not hasattr(providers.asr, "load"):
            return
        await sink.emit(
            "asr.progress",
            stage="loading",
            message="Loading ASR model for voice input.",
        )
        try:
            status = await providers.asr.load()
        except Exception as exc:
            await sink.emit(
                "asr.error",
                message=f"ASR preload failed: {exc}",
            )
            return
        await sink.emit(
            "asr.progress",
            stage="loaded",
            message=status.message,
        )
        await sink.emit(
            "providers.status",
            providers=await provider_statuses_for_bundle(providers),
            summary=profile_summary(await merged_profile_raw()),
            tts_runtime=await TTS_MANAGER.describe(),
        )

    async def sender() -> None:
        raw = await merged_profile_raw()
        await websocket.send_json(
            {"type": "profile.loaded", "payload": {"name": BASE_CONFIG.name}}
        )
        await websocket.send_json(
            {
                "type": "providers.status",
                "payload": {
                    "providers": await provider_statuses_payload(),
                    "summary": profile_summary(raw),
                    "tts_runtime": await TTS_MANAGER.describe(),
                },
            }
        )
        warm_task = asyncio.create_task(warm_asr_on_connect())
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        finally:
            warm_task.cancel()
            with suppress(asyncio.CancelledError):
                await warm_task

    async def receiver() -> None:
        nonlocal recording_utterance, recording_mode
        active_turn: asyncio.Task | None = None

        # Await cancellation before replacing turns so stale streams cannot race the next turn.
        async def cancel_active_turn(reason: str) -> None:
            nonlocal active_turn
            if active_turn and not active_turn.done():
                active_turn.cancel()
                with suppress(asyncio.CancelledError):
                    await active_turn
                await sink.emit("turn.cancelled", reason=reason)

        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            payload = message.get("payload", {})
            mode = str(payload.get("mode", "chat"))
            if mode not in ("chat", "companion"):
                mode = "chat"
            if message_type == "user.text":
                text = str(payload.get("text", "")).strip()
                RUNTIME_SETTINGS.active_mode = mode
                orchestrator.set_system_prompt(
                    str(payload.get("system_prompt", "")), mode=mode
                )
                if not text:
                    continue
                orchestrator.providers.tts = TTS_MANAGER.get_provider()
                await cancel_active_turn("new_user_text")
                active_turn = asyncio.create_task(
                    orchestrator.handle_text_turn(text, mode=mode)
                )
            elif message_type == "user.continue":
                RUNTIME_SETTINGS.active_mode = mode
                orchestrator.providers.tts = TTS_MANAGER.get_provider()
                await cancel_active_turn("continue")
                active_turn = asyncio.create_task(
                    orchestrator.handle_continue(mode=mode)
                )
            elif message_type == "system_prompt.update":
                RUNTIME_SETTINGS.active_mode = mode
                orchestrator.set_system_prompt(
                    str(payload.get("system_prompt", "")), mode=mode
                )
                await sink.emit(
                    "system_prompt.updated",
                    mode=mode,
                    enabled=bool(orchestrator.state.system_prompt),
                )
            elif message_type == "conversation.clear":
                await cancel_active_turn("conversation_clear")
                await orchestrator.clear_conversation(mode=mode)
            elif message_type == "tts.cancel":
                await orchestrator.cancel_tts("manual")
            elif message_type == "vad.speech_start":
                RUNTIME_SETTINGS.active_mode = mode
                orchestrator.set_system_prompt(
                    str(payload.get("system_prompt", orchestrator.state.system_prompt)),
                    mode=mode,
                )
                await orchestrator.cancel_tts("barge_in")
                await sink.emit("vad.speech_start", mode=mode, source="browser")
            elif message_type == "audio.frame":
                try:
                    frame = parse_audio_frame(payload, audio_stats)
                except ValueError as exc:
                    await sink.emit("audio.frame_error", message=str(exc))
                    continue
                pre_buffer.append(frame.data)
                if recording_utterance:
                    utterance_buffer.extend(frame.data)
                    if (
                        len(utterance_buffer)
                        > max_utterance_frames * expected_frame_bytes
                    ):
                        await sink.emit(
                            "asr.buffer_warning",
                            message="Maximum utterance length reached; closing current utterance.",
                        )
                        recording_utterance = False
                metrics = audio_stats.update(frame)
                if metrics:
                    await sink.emit("audio.input_level", **metrics)
                try:
                    vad_events = await providers.vad.process_frame(frame)
                except ValueError as exc:
                    await sink.emit("vad.error", message=str(exc))
                    continue
                for vad_event in vad_events:
                    if vad_event.type == "vad.speech_start":
                        recording_mode = mode
                        RUNTIME_SETTINGS.active_mode = recording_mode
                        if "system_prompt" in payload:
                            orchestrator.set_system_prompt(
                                str(payload.get("system_prompt", "")),
                                mode=recording_mode,
                            )
                        await orchestrator.cancel_tts("vad_barge_in")
                        utterance_buffer.clear()
                        for buffered_frame in pre_buffer:
                            utterance_buffer.extend(buffered_frame)
                        recording_utterance = True
                        await sink.emit(
                            "vad.speech_start",
                            mode=recording_mode,
                            source="silero",
                            probability=vad_event.probability,
                            audio_ms=vad_event.audio_ms,
                        )
                    elif vad_event.type == "vad.speech_end":
                        recording_utterance = False
                        pcm = bytes(utterance_buffer)
                        utterance_buffer.clear()
                        await sink.emit(
                            "vad.speech_end",
                            mode=recording_mode,
                            source="silero",
                            probability=vad_event.probability,
                            audio_ms=vad_event.audio_ms,
                        )
                        if min_speech_duration_ms > 0 and pcm:
                            duration_ms = len(pcm) // max(bytes_per_ms, 1)
                            if duration_ms < min_speech_duration_ms:
                                await sink.emit(
                                    "vad.speech_rejected",
                                    mode=recording_mode,
                                    duration_ms=duration_ms,
                                    min_duration_ms=min_speech_duration_ms,
                                )
                                pcm = b""
                        if (
                            pcm
                            and reject_low_energy_rms > 0
                            and reject_low_energy_max_duration_ms > 0
                        ):
                            duration_ms = len(pcm) // max(bytes_per_ms, 1)
                            level = compute_pcm16_level(pcm)
                            if (
                                duration_ms <= reject_low_energy_max_duration_ms
                                and level["rms"] < reject_low_energy_rms
                            ):
                                await sink.emit(
                                    "vad.speech_rejected",
                                    mode=recording_mode,
                                    duration_ms=duration_ms,
                                    rms=level["rms"],
                                    min_rms=reject_low_energy_rms,
                                    reason="low_energy",
                                )
                                pcm = b""
                        if pcm and reject_utterance_rms > 0:
                            duration_ms = len(pcm) // max(bytes_per_ms, 1)
                            level = compute_pcm16_level(pcm)
                            if level["rms"] < reject_utterance_rms:
                                await sink.emit(
                                    "vad.speech_rejected",
                                    mode=recording_mode,
                                    duration_ms=duration_ms,
                                    rms=level["rms"],
                                    min_rms=reject_utterance_rms,
                                    reason="utterance_low_energy",
                                )
                                pcm = b""
                        if pcm and trim_silence_rms > 0:
                            original_duration_ms = len(pcm) // max(bytes_per_ms, 1)
                            pcm = trim_pcm16_silence(
                                pcm,
                                frame_ms=trim_silence_frame_ms,
                                sample_rate=audio_stats.expected_sample_rate,
                                rms_threshold=trim_silence_rms,
                            )
                            trimmed_duration_ms = len(pcm) // max(bytes_per_ms, 1)
                            if trimmed_duration_ms != original_duration_ms:
                                await sink.emit(
                                    "asr.audio_trimmed",
                                    mode=recording_mode,
                                    original_duration_ms=original_duration_ms,
                                    trimmed_duration_ms=trimmed_duration_ms,
                                    rms_threshold=trim_silence_rms,
                                )
                        if pcm and (not active_turn or active_turn.done()):
                            RUNTIME_SETTINGS.active_mode = recording_mode
                            orchestrator.providers.tts = TTS_MANAGER.get_provider()
                            active_turn = asyncio.create_task(
                                orchestrator.handle_audio_turn(
                                    pcm,
                                    audio_stats.expected_sample_rate,
                                    mode=recording_mode,
                                )
                            )
                    elif vad_event.type == "vad.probability":
                        await sink.emit(
                            "vad.probability",
                            mode=mode,
                            probability=vad_event.probability,
                            audio_ms=vad_event.audio_ms,
                        )
            elif message_type == "ping":
                await sink.emit("pong")

    sender_task = asyncio.create_task(sender())
    receiver_task = asyncio.create_task(receiver())
    try:
        done, pending = await asyncio.wait(
            {sender_task, receiver_task},
            return_when=asyncio.FIRST_EXCEPTION,
        )
        for task in done:
            task.result()
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        sender_task.cancel()
        receiver_task.cancel()
    finally:
        ACTIVE_QUEUES.discard(queue)
        TTS_CANCEL_HOOKS.discard(cancel_hook)
        TURN_CONFIG_HOOKS.discard(turn_config_hook)
        CHARACTER_SEED_HOOKS.discard(seed_hook)
        COMPANION_HISTORY_HOOKS.discard(companion_history_hook)
