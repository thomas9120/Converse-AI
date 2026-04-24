import os

import pytest

from conversational_harness.providers.llamacpp import LlamaCppProvider


@pytest.mark.skipif(os.environ.get("HARNESS_TEST_LLAMA_CPP") != "1", reason="live llama.cpp test is opt-in")
def test_live_llamacpp_streams_tokens():
    import asyncio

    async def run_live():
        provider = LlamaCppProvider(
            {
                "base_url": os.environ.get("HARNESS_LLAMA_CPP_URL", "http://127.0.0.1:8080"),
                "model": os.environ.get("HARNESS_LLAMA_CPP_MODEL", "auto"),
                "max_tokens": 24,
                "temperature": 0,
            }
        )
        status = await provider.check_status()
        assert status.ready, status.message
        tokens = []
        async for token in provider.stream_response([{"role": "user", "content": "Reply with the word ready."}]):
            tokens.append(token)
            if len("".join(tokens)) > 12:
                break
        return "".join(tokens)

    response = asyncio.run(run_live())

    assert response.strip()
