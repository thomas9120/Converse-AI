import asyncio

from conversational_harness.config import load_config
from conversational_harness.orchestrator import ConversationOrchestrator, QueueEventSink, should_flush_tts
from conversational_harness.providers import build_providers
from conversational_harness.runtime_settings import CharacterCard, RuntimeSettings


class RecordingLLM:
    def __init__(self):
        self.messages = []

    @property
    def status(self):
        raise NotImplementedError

    async def check_status(self):
        raise NotImplementedError

    async def stream_response(self, messages):
        self.messages.append(messages)
        yield "ok."


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
    assert payload["chunk_index"] == 1
    assert payload["text_chars"] > 0
    assert payload["byte_length"] > 0
    assert payload["turn_id"] == 1
    assert payload["mime_type"] == "audio/wav"


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


def test_system_prompt_is_prepended_to_llm_messages():
    async def run_turn():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        llm = RecordingLLM()
        providers.llm = llm
        queue = asyncio.Queue()
        orchestrator = ConversationOrchestrator(providers, QueueEventSink(queue), tts_chunk_chars=60)

        orchestrator.set_system_prompt("Answer like a terse local assistant.")
        await orchestrator.handle_text_turn("hello harness")
        await asyncio.sleep(0.05)
        return llm.messages[0]

    messages = asyncio.run(run_turn())

    assert messages[0] == {"role": "system", "content": "Answer like a terse local assistant."}
    assert messages[1] == {"role": "user", "content": "hello harness"}


def test_no_system_prompt_is_sent_when_unset():
    async def run_turn():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        llm = RecordingLLM()
        providers.llm = llm
        queue = asyncio.Queue()
        orchestrator = ConversationOrchestrator(providers, QueueEventSink(queue), tts_chunk_chars=60)

        await orchestrator.handle_text_turn("hello harness")
        await asyncio.sleep(0.05)
        return llm.messages[0]

    messages = asyncio.run(run_turn())

    assert messages == [{"role": "user", "content": "hello harness"}]


def test_character_first_message_seeds_empty_conversation():
    async def run_seed():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        queue = asyncio.Queue()
        settings = RuntimeSettings(
            user_name="Mara",
            ai_name="Assistant",
            character=CharacterCard(name="Lyra", first_mes="Hello, {{user}}. I am {{char}}."),
        )
        orchestrator = ConversationOrchestrator(
            providers,
            QueueEventSink(queue),
            tts_chunk_chars=60,
            runtime_settings=settings,
        )

        seeded = await orchestrator.seed_character_first_message()
        event = await queue.get()
        return seeded, orchestrator.state.messages, event

    seeded, messages, event = asyncio.run(run_seed())

    assert seeded is True
    assert messages == [{"role": "assistant", "content": "Hello, Mara. I am Lyra."}]
    assert event["type"] == "conversation.seeded"
    assert event["payload"] == {"role": "assistant", "text": "Hello, Mara. I am Lyra."}


def test_character_first_message_seed_is_noop_when_history_exists():
    async def run_seed():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        queue = asyncio.Queue()
        settings = RuntimeSettings(character=CharacterCard(name="Lyra", first_mes="Hello."))
        orchestrator = ConversationOrchestrator(
            providers,
            QueueEventSink(queue),
            tts_chunk_chars=60,
            runtime_settings=settings,
        )
        orchestrator.state.messages.append({"role": "user", "content": "Already here"})

        seeded = await orchestrator.seed_character_first_message()
        return seeded, orchestrator.state.messages, queue.empty()

    seeded, messages, no_events = asyncio.run(run_seed())

    assert seeded is False
    assert messages == [{"role": "user", "content": "Already here"}]
    assert no_events is True


def test_clear_conversation_removes_history():
    async def run_turn():
        config = load_config("profiles/mock-local.json")
        providers = build_providers(config)
        queue = asyncio.Queue()
        orchestrator = ConversationOrchestrator(providers, QueueEventSink(queue), tts_chunk_chars=60)

        await orchestrator.handle_text_turn("hello harness")
        await asyncio.sleep(0.15)
        assert orchestrator.state.messages

        await orchestrator.clear_conversation()
        events = []
        while not queue.empty():
            events.append((await queue.get())["type"])
        return orchestrator.state.messages, events

    messages, events = asyncio.run(run_turn())

    assert messages == []
    assert "conversation.cleared" in events
