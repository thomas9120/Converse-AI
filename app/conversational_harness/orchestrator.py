from __future__ import annotations

import asyncio
import base64
import time
from dataclasses import dataclass, field
from typing import Any

from conversational_harness.events import EventSink
from conversational_harness.providers.factory import ProviderBundle


@dataclass
class TurnState:
    messages: list[dict[str, str]] = field(default_factory=list)
    active_tts_tasks: set[asyncio.Task] = field(default_factory=set)


class ConversationOrchestrator:
    def __init__(
        self,
        providers: ProviderBundle,
        sink: EventSink,
        tts_chunk_chars: int = 120,
        min_tts_chars: int = 0,
    ):
        self.providers = providers
        self.sink = sink
        self.tts_chunk_chars = tts_chunk_chars
        self.min_tts_chars = min_tts_chars
        self.state = TurnState()

    async def cancel_tts(self, reason: str) -> None:
        active = [task for task in self.state.active_tts_tasks if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await self.sink.emit("tts.cancelled", reason=reason)

    async def handle_text_turn(self, text: str) -> None:
        started = time.perf_counter()
        await self.cancel_tts("new_user_turn")
        await self.sink.emit("turn.started")
        await self.sink.emit("vad.speech_start", source="text")

        final_transcript = ""
        async for transcript in self.providers.asr.transcribe_text_input(text):
            await self.sink.emit(
                "asr.transcript",
                text=transcript.text,
                final=transcript.final,
                latency_ms=elapsed_ms(started),
            )
            if transcript.final:
                final_transcript = transcript.text

        await self.sink.emit("vad.speech_end", source="text", latency_ms=elapsed_ms(started))
        if not final_transcript:
            await self.sink.emit("turn.finished", reason="empty_transcript")
            return

        await self._respond_to_transcript(final_transcript, started)

    async def handle_audio_turn(self, pcm_s16le: bytes, sample_rate: int) -> None:
        started = time.perf_counter()
        await self.cancel_tts("new_audio_turn")
        await self.sink.emit("turn.started", source="audio")
        await self.sink.emit("asr.started", sample_rate=sample_rate, bytes=len(pcm_s16le))

        final_transcript = ""
        try:
            async def progress(event_type: str, payload: dict) -> None:
                await self.sink.emit(event_type, **payload, latency_ms=elapsed_ms(started))

            async for transcript in self.providers.asr.transcribe_audio(pcm_s16le, sample_rate, progress):
                await self.sink.emit(
                    "asr.transcript",
                    text=transcript.text,
                    final=transcript.final,
                    latency_ms=elapsed_ms(started),
                )
                if transcript.final:
                    final_transcript = transcript.text
        except Exception as exc:
            await self.sink.emit("asr.error", message=str(exc), latency_ms=elapsed_ms(started))
            await self.sink.emit("turn.finished", reason="asr_error", latency_ms=elapsed_ms(started))
            return

        if not final_transcript:
            await self.sink.emit("turn.finished", reason="empty_transcript", latency_ms=elapsed_ms(started))
            return

        await self._respond_to_transcript(final_transcript, started)

    async def _respond_to_transcript(self, final_transcript: str, started: float) -> None:
        self.state.messages.append({"role": "user", "content": final_transcript})
        response_text = ""
        first_token_seen = False
        sentence_buffer = ""

        try:
            async for token in self.providers.llm.stream_response(self.state.messages):
                if not first_token_seen:
                    first_token_seen = True
                    await self.sink.emit("llm.first_token", latency_ms=elapsed_ms(started))
                response_text += token
                sentence_buffer += token
                await self.sink.emit("llm.token", text=token, accumulated=response_text)

                if should_flush_tts(sentence_buffer, self.tts_chunk_chars, self.min_tts_chars):
                    await self._start_tts_chunk(sentence_buffer.strip(), started)
                    sentence_buffer = ""

            if sentence_buffer.strip():
                await self._start_tts_chunk(sentence_buffer.strip(), started)

            self.state.messages.append({"role": "assistant", "content": response_text.strip()})
            await self.sink.emit("turn.finished", latency_ms=elapsed_ms(started))
        except Exception as exc:
            await self.sink.emit("turn.error", message=str(exc), latency_ms=elapsed_ms(started))

    async def _start_tts_chunk(self, text: str, turn_started: float) -> None:
        task = asyncio.create_task(self._stream_tts(text, turn_started))
        self.state.active_tts_tasks.add(task)
        task.add_done_callback(self.state.active_tts_tasks.discard)

    async def _stream_tts(self, text: str, turn_started: float) -> None:
        first_chunk_seen = False
        try:
            async def progress(event_type: str, payload: dict) -> None:
                await self.sink.emit(event_type, **payload, latency_ms=elapsed_ms(turn_started))

            async for chunk in self.providers.tts.stream_audio_with_progress(text, progress):
                if not first_chunk_seen:
                    first_chunk_seen = True
                    await self.sink.emit("tts.first_chunk", latency_ms=elapsed_ms(turn_started), text=text)
                encoded = base64.b64encode(chunk.data).decode("ascii")
                await self.sink.emit(
                    "tts.audio",
                    mime_type=chunk.mime_type,
                    data=encoded,
                    final=chunk.final,
                    text=text,
                    latency_ms=elapsed_ms(turn_started),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.sink.emit("tts.error", message=str(exc), latency_ms=elapsed_ms(turn_started), text=text)


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def should_flush_tts(text: str, limit: int, minimum: int = 0) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if len(stripped) >= limit:
        return True
    if len(stripped) < minimum:
        return False
    return stripped.endswith((".", "!", "?", ";", ":"))


class QueueEventSink(EventSink):
    def __init__(self, queue: asyncio.Queue[dict[str, Any]]):
        self.queue = queue

    async def emit(self, event_type: str, **payload: Any) -> None:
        await self.queue.put({"type": event_type, "ts": time.perf_counter(), "payload": payload})
