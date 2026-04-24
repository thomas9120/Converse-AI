from __future__ import annotations

import asyncio
import logging
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from conversational_harness.audio_frames import AudioFrameStats, parse_audio_frame
from conversational_harness.config import PROJECT_ROOT, load_config
from conversational_harness.orchestrator import ConversationOrchestrator, QueueEventSink
from conversational_harness.providers.factory import build_provider_bundle, serialize_statuses
from conversational_harness.tts_runtime import TTSRuntimeManager, load_tts_presets

logger = logging.getLogger(__name__)

STATIC_ROOT = PROJECT_ROOT / "app" / "static"

BASE_CONFIG = load_config()
TTS_MANAGER = TTSRuntimeManager(BASE_CONFIG, load_tts_presets())
ACTIVE_QUEUES: set[asyncio.Queue[dict[str, Any]]] = set()
TTS_CANCEL_HOOKS: set[Any] = set()
TURN_CONFIG_HOOKS: set[Any] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    logger.info("Shutting down: cleaning up providers and active connections.")
    await cancel_active_tts("shutdown")
    providers = build_provider_bundle(BASE_CONFIG, tts_provider=TTS_MANAGER.get_provider())
    for provider in (providers.vad, providers.asr, providers.llm, providers.tts):
        try:
            if hasattr(provider, "unload"):
                await provider.unload()
        except Exception:
            pass
    for queue in list(ACTIVE_QUEUES):
        ACTIVE_QUEUES.discard(queue)


app = FastAPI(title="Conversational AI Harness", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

BASE_CONFIG = load_config()
TTS_MANAGER = TTSRuntimeManager(BASE_CONFIG, load_tts_presets())
ACTIVE_QUEUES: set[asyncio.Queue[dict[str, Any]]] = set()
TTS_CANCEL_HOOKS: set[Any] = set()
TURN_CONFIG_HOOKS: set[Any] = set()


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
    providers = build_provider_bundle(BASE_CONFIG, tts_provider=TTS_MANAGER.get_provider())
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


async def cancel_active_tts(reason: str) -> None:
    for hook in list(TTS_CANCEL_HOOKS):
        await hook(reason)


async def refresh_turn_config() -> None:
    config = TTS_MANAGER.current_turn_config()
    for hook in list(TURN_CONFIG_HOOKS):
        hook(config)


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    providers = build_provider_bundle(BASE_CONFIG, tts_provider=TTS_MANAGER.get_provider())
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
    min_speech_duration_ms = int(vad_config.get("min_speech_duration_ms", 0))
    pre_speech_frames = int(audio_config.get("pre_speech_ms", 450)) // audio_stats.expected_frame_ms
    max_utterance_frames = int(audio_config.get("max_utterance_ms", 30000)) // audio_stats.expected_frame_ms
    bytes_per_ms = audio_stats.expected_sample_rate * 2 // 1000
    pre_buffer: deque[bytes] = deque(maxlen=max(1, pre_speech_frames))
    utterance_buffer = bytearray()
    recording_utterance = False
    turn_config = TTS_MANAGER.current_turn_config()
    orchestrator = ConversationOrchestrator(
        providers=providers,
        sink=sink,
        tts_chunk_chars=int(turn_config.get("tts_chunk_chars", 120)),
        min_tts_chars=int(turn_config.get("min_tts_chars", 0)),
    )

    async def cancel_hook(reason: str) -> None:
        await orchestrator.cancel_tts(reason)

    def turn_config_hook(config: dict[str, Any]) -> None:
        orchestrator.update_turn_config(
            tts_chunk_chars=int(config.get("tts_chunk_chars", 120)),
            min_tts_chars=int(config.get("min_tts_chars", 0)),
        )
        orchestrator.providers.tts = TTS_MANAGER.get_provider()

    TTS_CANCEL_HOOKS.add(cancel_hook)
    TURN_CONFIG_HOOKS.add(turn_config_hook)

    async def sender() -> None:
        raw = await merged_profile_raw()
        await websocket.send_json({"type": "profile.loaded", "payload": {"name": BASE_CONFIG.name}})
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
        while True:
            event = await queue.get()
            await websocket.send_json(event)

    async def receiver() -> None:
        nonlocal recording_utterance
        active_turn: asyncio.Task | None = None
        while True:
            message = await websocket.receive_json()
            message_type = message.get("type")
            payload = message.get("payload", {})
            if message_type == "user.text":
                text = str(payload.get("text", "")).strip()
                orchestrator.set_system_prompt(str(payload.get("system_prompt", "")))
                if not text:
                    continue
                orchestrator.providers.tts = TTS_MANAGER.get_provider()
                if active_turn and not active_turn.done():
                    active_turn.cancel()
                    await sink.emit("turn.cancelled", reason="new_user_text")
                active_turn = asyncio.create_task(orchestrator.handle_text_turn(text))
            elif message_type == "system_prompt.update":
                orchestrator.set_system_prompt(str(payload.get("system_prompt", "")))
                await sink.emit("system_prompt.updated", enabled=bool(orchestrator.state.system_prompt))
            elif message_type == "conversation.clear":
                if active_turn and not active_turn.done():
                    active_turn.cancel()
                    await sink.emit("turn.cancelled", reason="conversation_clear")
                await orchestrator.clear_conversation()
            elif message_type == "tts.cancel":
                await orchestrator.cancel_tts("manual")
            elif message_type == "vad.speech_start":
                orchestrator.set_system_prompt(str(payload.get("system_prompt", orchestrator.state.system_prompt)))
                await orchestrator.cancel_tts("barge_in")
                await sink.emit("vad.speech_start", source="browser")
            elif message_type == "audio.frame":
                try:
                    frame = parse_audio_frame(payload, audio_stats)
                except ValueError as exc:
                    await sink.emit("audio.frame_error", message=str(exc))
                    continue
                pre_buffer.append(frame.data)
                if recording_utterance:
                    utterance_buffer.extend(frame.data)
                    if len(utterance_buffer) // len(frame.data) > max_utterance_frames:
                        await sink.emit("asr.buffer_warning", message="Maximum utterance length reached; closing current utterance.")
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
                        await orchestrator.cancel_tts("vad_barge_in")
                        utterance_buffer.clear()
                        for buffered_frame in pre_buffer:
                            utterance_buffer.extend(buffered_frame)
                        recording_utterance = True
                        await sink.emit(
                            "vad.speech_start",
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
                            source="silero",
                            probability=vad_event.probability,
                            audio_ms=vad_event.audio_ms,
                        )
                        if min_speech_duration_ms > 0 and pcm:
                            duration_ms = len(pcm) // max(bytes_per_ms, 1)
                            if duration_ms < min_speech_duration_ms:
                                await sink.emit(
                                    "vad.speech_rejected",
                                    duration_ms=duration_ms,
                                    min_duration_ms=min_speech_duration_ms,
                                )
                                pcm = b""
                        if pcm and (not active_turn or active_turn.done()):
                            orchestrator.providers.tts = TTS_MANAGER.get_provider()
                            active_turn = asyncio.create_task(
                                orchestrator.handle_audio_turn(pcm, audio_stats.expected_sample_rate)
                            )
                    elif vad_event.type == "vad.probability":
                        await sink.emit(
                            "vad.probability",
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
