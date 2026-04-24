import asyncio

from conversational_harness.config import load_config
from conversational_harness.orchestrator import ConversationOrchestrator, QueueEventSink, should_flush_tts
from conversational_harness.providers import build_providers


def test_should_flush_tts_on_sentence_or_limit():
    assert should_flush_tts("Hello there.", 120)
    assert should_flush_tts("x" * 121, 120)
    assert not should_flush_tts("still speaking", 120)
    assert not should_flush_tts("Too soon.", 120, 20)
    assert should_flush_tts("This sentence is long enough.", 120, 20)


def test_text_turn_emits_core_events():
    async def run_turn():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        queue = asyncio.Queue()
        orchestrator = ConversationOrchestrator(providers, QueueEventSink(queue), tts_chunk_chars=60)

        await orchestrator.handle_text_turn("hello harness")
        await asyncio.sleep(0.15)

        events = []
        while not queue.empty():
            events.append((await queue.get())["type"])
        return events

    events = asyncio.run(run_turn())

    assert "turn.started" in events
    assert "asr.transcript" in events
    assert "llm.first_token" in events
    assert "llm.token" in events
    assert "tts.first_chunk" in events
    assert "tts.audio" in events
    assert "turn.finished" in events


def test_tts_audio_event_carries_latency():
    async def run_turn():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        queue = asyncio.Queue()
        orchestrator = ConversationOrchestrator(providers, QueueEventSink(queue), tts_chunk_chars=60)

        await orchestrator.handle_text_turn("hello harness")
        await asyncio.sleep(0.15)

        while not queue.empty():
            event = await queue.get()
            if event["type"] == "tts.audio":
                return event["payload"]
        return {}

    payload = asyncio.run(run_turn())

    assert isinstance(payload.get("latency_ms"), int)
    assert payload["latency_ms"] >= 0


def test_audio_turn_emits_asr_and_llm_events():
    async def run_turn():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        queue = asyncio.Queue()
        orchestrator = ConversationOrchestrator(providers, QueueEventSink(queue), tts_chunk_chars=60)

        await orchestrator.handle_audio_turn(b"\x00\x00" * 1600, 16000)
        await asyncio.sleep(0.15)

        events = []
        while not queue.empty():
            events.append((await queue.get())["type"])
        return events

    events = asyncio.run(run_turn())

    assert "asr.started" in events
    assert "asr.transcript" in events
    assert "llm.first_token" in events
    assert "turn.finished" in events
