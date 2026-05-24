from __future__ import annotations

import asyncio
import base64
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from conversational_harness.events import EventSink
from conversational_harness.providers.factory import ProviderBundle

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from conversational_harness.runtime_settings import RuntimeSettings


@dataclass
class TurnState:
    messages: list[dict[str, str]] = field(default_factory=list)
    active_tts_tasks: set[asyncio.Task] = field(default_factory=set)
    system_prompt: str = ""
    turn_id: int = 0
    tts_tail: asyncio.Task | None = None


class ConversationOrchestrator:
    def __init__(
        self,
        providers: ProviderBundle,
        sink: EventSink,
        tts_chunk_chars: int = 120,
        min_tts_chars: int = 0,
        runtime_settings: RuntimeSettings | None = None,
    ):
        self.providers = providers
        self.sink = sink
        self.tts_chunk_chars = tts_chunk_chars
        self.min_tts_chars = min_tts_chars
        self.state = TurnState()
        self._runtime_settings = runtime_settings

    def update_turn_config(self, *, tts_chunk_chars: int, min_tts_chars: int) -> None:
        self.tts_chunk_chars = tts_chunk_chars
        self.min_tts_chars = min_tts_chars

    async def clear_conversation(self) -> None:
        await self.cancel_tts("conversation_clear")
        self.state.messages.clear()
        await self.sink.emit("conversation.cleared")

    async def seed_character_first_message(self) -> bool:
        if self._runtime_settings is None or self._runtime_settings.character is None:
            return False
        # First messages start empty chats, but should never overwrite an active conversation.
        if self.state.messages:
            return False
        text = self._runtime_settings.character.first_message(
            self._runtime_settings.display_name_user(),
            self._runtime_settings.display_name_ai(),
        )
        if not text:
            return False
        self.state.messages.append({"role": "assistant", "content": text})
        await self.sink.emit("conversation.seeded", role="assistant", text=text)
        return True

    def set_system_prompt(self, prompt: str) -> None:
        self.state.system_prompt = prompt.strip()

    async def cancel_tts(self, reason: str) -> None:
        active = [task for task in self.state.active_tts_tasks if not task.done()]
        for task in active:
            task.cancel()
        if active:
            await asyncio.gather(*active, return_exceptions=True)
        self.state.tts_tail = None
        if active:
            await self.sink.emit("tts.cancelled", reason=reason)

    async def handle_text_turn(self, text: str) -> None:
        started = time.perf_counter()
        turn_id = self._next_turn_id()
        await self.cancel_tts("new_user_turn")
        await self.sink.emit("turn.started", turn_id=turn_id)
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

        await self.sink.emit(
            "vad.speech_end", source="text", latency_ms=elapsed_ms(started)
        )
        if not final_transcript:
            await self.sink.emit("turn.finished", reason="empty_transcript")
            return

        await self._respond_to_transcript(final_transcript, started, turn_id)

    async def handle_audio_turn(self, pcm_s16le: bytes, sample_rate: int) -> None:
        started = time.perf_counter()
        turn_id = self._next_turn_id()
        await self.cancel_tts("new_audio_turn")
        await self.sink.emit("turn.started", source="audio", turn_id=turn_id)
        await self.sink.emit(
            "asr.started", sample_rate=sample_rate, bytes=len(pcm_s16le)
        )

        final_transcript = ""
        try:

            async def progress(event_type: str, payload: dict) -> None:
                await self.sink.emit(
                    event_type, **payload, latency_ms=elapsed_ms(started)
                )

            async for transcript in self.providers.asr.transcribe_audio(
                pcm_s16le, sample_rate, progress
            ):
                await self.sink.emit(
                    "asr.transcript",
                    text=transcript.text,
                    final=transcript.final,
                    latency_ms=elapsed_ms(started),
                )
                if transcript.final:
                    final_transcript = transcript.text
        except Exception as exc:
            await self.sink.emit(
                "asr.error", message=str(exc), latency_ms=elapsed_ms(started)
            )
            await self.sink.emit(
                "turn.finished", reason="asr_error", latency_ms=elapsed_ms(started)
            )
            return

        if not final_transcript:
            await self.sink.emit(
                "turn.finished",
                reason="empty_transcript",
                latency_ms=elapsed_ms(started),
            )
            return

        await self._respond_to_transcript(final_transcript, started, turn_id)

    async def handle_continue(self) -> None:
        if not self.state.messages or self.state.messages[-1]["role"] != "assistant":
            await self.sink.emit(
                "turn.error", message="No previous assistant message to continue."
            )
            return
        started = time.perf_counter()
        turn_id = self._next_turn_id()
        await self.cancel_tts("continue_turn")
        await self.sink.emit("turn.started", source="continue", turn_id=turn_id)
        prefix = self.state.messages[-1]["content"]
        self.state.messages.pop()
        self.state.messages.append({"role": "assistant", "content": prefix})

        try:
            response_text = await self._stream_llm_and_tts(prefix, started, turn_id)
            self.state.messages[-1] = {
                "role": "assistant",
                "content": response_text.strip(),
            }
            await self.sink.emit("turn.finished", latency_ms=elapsed_ms(started))
        except Exception as exc:
            await self.sink.emit(
                "turn.error", message=str(exc), latency_ms=elapsed_ms(started)
            )

    async def _respond_to_transcript(
        self, final_transcript: str, started: float, turn_id: int
    ) -> None:
        self.state.messages.append({"role": "user", "content": final_transcript})
        try:
            response_text = await self._stream_llm_and_tts("", started, turn_id)
            self.state.messages.append(
                {"role": "assistant", "content": response_text.strip()}
            )
            await self.sink.emit("turn.finished", latency_ms=elapsed_ms(started))
        except Exception as exc:
            await self.sink.emit(
                "turn.error", message=str(exc), latency_ms=elapsed_ms(started)
            )

    async def _stream_llm_and_tts(
        self, response_text: str, started: float, turn_id: int
    ) -> str:
        first_token_seen = False
        sentence_buffer = ""
        async for token in self.providers.llm.stream_response(self._llm_messages()):
            if not first_token_seen:
                first_token_seen = True
                await self.sink.emit("llm.first_token", latency_ms=elapsed_ms(started))
            response_text += token
            sentence_buffer += token
            await self.sink.emit("llm.token", text=token, accumulated=response_text)

            if should_flush_tts(
                sentence_buffer, self.tts_chunk_chars, self.min_tts_chars
            ):
                await self._start_tts_chunk(sentence_buffer.strip(), started, turn_id)
                sentence_buffer = ""

        if sentence_buffer.strip():
            await self._start_tts_chunk(sentence_buffer.strip(), started, turn_id)
        return response_text

    async def _start_tts_chunk(
        self, text: str, turn_started: float, turn_id: int
    ) -> None:
        previous = self.state.tts_tail
        task = asyncio.create_task(
            self._stream_tts_after(previous, text, turn_started, turn_id)
        )
        self.state.tts_tail = task
        self.state.active_tts_tasks.add(task)
        task.add_done_callback(self.state.active_tts_tasks.discard)

    async def _stream_tts_after(
        self,
        previous: asyncio.Task | None,
        text: str,
        turn_started: float,
        turn_id: int,
    ) -> None:
        if previous is not None:
            try:
                await previous
            except Exception as exc:
                logger.warning("Previous TTS task failed: %s", exc)
        await self._stream_tts(text, turn_started, turn_id)

    async def _stream_tts(self, text: str, turn_started: float, turn_id: int) -> None:
        first_chunk_seen = False
        chunk_index = 0
        try:

            async def progress(event_type: str, payload: dict) -> None:
                await self.sink.emit(
                    event_type, **payload, latency_ms=elapsed_ms(turn_started)
                )

            async for chunk in self.providers.tts.stream_audio_with_progress(
                text, progress
            ):
                chunk_index += 1
                if not first_chunk_seen:
                    first_chunk_seen = True
                    await self.sink.emit(
                        "tts.first_chunk",
                        latency_ms=elapsed_ms(turn_started),
                        text=text,
                        turn_id=turn_id,
                    )
                encoded = base64.b64encode(chunk.data).decode("ascii")
                await self.sink.emit(
                    "tts.audio",
                    mime_type=chunk.mime_type,
                    sample_rate=chunk.sample_rate,
                    channels=chunk.channels,
                    encoding=chunk.encoding,
                    duration_ms=chunk.duration_ms,
                    data=encoded,
                    final=chunk.final,
                    text=text,
                    turn_id=turn_id,
                    chunk_index=chunk_index,
                    text_chars=len(text),
                    byte_length=len(chunk.data),
                    latency_ms=elapsed_ms(turn_started),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.sink.emit(
                "tts.error",
                message=str(exc),
                latency_ms=elapsed_ms(turn_started),
                text=text,
            )

    def _llm_messages(self) -> list[dict[str, str]]:
        prompt = self._effective_system_prompt()
        if not prompt:
            return list(self.state.messages)
        return [{"role": "system", "content": prompt}, *self.state.messages]

    def _effective_system_prompt(self) -> str:
        if self._runtime_settings is not None:
            return self._runtime_settings.effective_system_prompt(
                self.state.system_prompt
            )
        if self.state.system_prompt:
            return self.state.system_prompt
        return ""

    def _next_turn_id(self) -> int:
        self.state.turn_id += 1
        return self.state.turn_id


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
        await self.queue.put(
            {"type": event_type, "ts": time.perf_counter(), "payload": payload}
        )
