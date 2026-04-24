from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from conversational_harness.audio_frames import AudioFrameStats, parse_audio_frame
from conversational_harness.config import PROJECT_ROOT, load_config
from conversational_harness.orchestrator import ConversationOrchestrator, QueueEventSink
from conversational_harness.providers import build_providers


STATIC_ROOT = PROJECT_ROOT / "app" / "static"

app = FastAPI(title="Conversational AI Harness")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    config = load_config()
    providers = build_providers(config)
    provider_statuses = await providers.check_statuses()
    return {
        "profile": {
            "name": config.name,
            "path": str(config.path),
            "description": config.raw.get("description", ""),
            "audio": config.section("audio"),
            "turn": config.section("turn"),
        },
        "providers": provider_statuses,
    }


@app.websocket("/ws/events")
async def websocket_events(websocket: WebSocket) -> None:
    await websocket.accept()
    config = load_config()
    providers = build_providers(config)
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    sink = QueueEventSink(queue)
    audio_config = config.section("audio")
    audio_stats = AudioFrameStats(
        expected_sample_rate=int(audio_config.get("sample_rate", 16000)),
        expected_channels=int(audio_config.get("channels", 1)),
        expected_frame_ms=int(audio_config.get("frame_ms", 30)),
    )
    pre_speech_frames = int(audio_config.get("pre_speech_ms", 450)) // audio_stats.expected_frame_ms
    max_utterance_frames = int(audio_config.get("max_utterance_ms", 30000)) // audio_stats.expected_frame_ms
    pre_buffer: deque[bytes] = deque(maxlen=max(1, pre_speech_frames))
    utterance_buffer = bytearray()
    recording_utterance = False
    turn_config = config.section("turn")
    orchestrator = ConversationOrchestrator(
        providers=providers,
        sink=sink,
        tts_chunk_chars=int(turn_config.get("tts_chunk_chars", 120)),
        min_tts_chars=int(turn_config.get("min_tts_chars", 0)),
    )

    async def sender() -> None:
        await websocket.send_json({"type": "profile.loaded", "payload": {"name": config.name}})
        await websocket.send_json({"type": "providers.status", "payload": {"providers": await providers.check_statuses()}})
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
                if not text:
                    continue
                if active_turn and not active_turn.done():
                    active_turn.cancel()
                    await sink.emit("turn.cancelled", reason="new_user_text")
                active_turn = asyncio.create_task(orchestrator.handle_text_turn(text))
            elif message_type == "vad.speech_start":
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
                        if pcm and (not active_turn or active_turn.done()):
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
