from conversational_harness import doctor
from conversational_harness.providers.base import ProviderCapabilities, ProviderStatus


class HealthyProvider:
    async def check_status(self):
        return ProviderStatus(
            name="healthy",
            kind="asr",
            ready=True,
            message="ready",
            capabilities=ProviderCapabilities(),
        )


class FailingProvider:
    async def check_status(self):
        raise RuntimeError("boom")


def test_llamacpp_not_selected():
    check = doctor.check_llamacpp({"provider": "mock"})

    assert check.ok
    assert "not selected" in check.detail


def test_llamacpp_unreachable(monkeypatch):
    monkeypatch.setattr(doctor, "fetch_json", lambda url, timeout: None)

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:9999"})

    assert not check.ok
    assert "not reachable" in check.detail


def test_llamacpp_loading(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "loading", "error": {"message": "Loading model"}}
        raise AssertionError(f"/v1/models must not be called: {url}")

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:8080"})

    assert not check.ok
    assert "not ready" in check.detail
    assert "Loading model" in check.detail


def test_harness_port_is_fatal_check():
    assert "Harness port" in doctor.FATAL_CHECKS


def test_llamacpp_health_ok_but_models_missing(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": []}

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:8080"})

    assert not check.ok
    assert "no model" in check.detail


def test_llamacpp_ready(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": [{"id": "local-gguf"}]}

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)

    check = doctor.check_llamacpp({"provider": "llamacpp", "base_url": "http://127.0.0.1:8080"})

    assert check.ok
    assert "local-gguf" in check.detail


def test_llamacpp_configured_model_mismatch(monkeypatch):
    def fake_fetch(url, timeout):
        if url.endswith("/health"):
            return {"status": "ok"}
        return {"data": [{"id": "actual-model"}]}

    monkeypatch.setattr(doctor, "fetch_json", fake_fetch)

    check = doctor.check_llamacpp(
        {"provider": "llamacpp", "base_url": "http://127.0.0.1:8080", "model": "missing-model"}
    )

    assert not check.ok
    assert "not loaded" in check.detail


def test_find_port_owner_parses_windows_netstat(monkeypatch):
    monkeypatch.setattr(doctor.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        doctor.subprocess,
        "check_output",
        lambda *args, **kwargs: "  TCP    127.0.0.1:7860    0.0.0.0:0    LISTENING    4321\r\n",
    )
    monkeypatch.setattr(doctor, "find_process_command", lambda pid: "python -m uvicorn app")

    owner = doctor.find_port_owner(7860)

    assert owner == {"pid": "4321", "command": "python -m uvicorn app"}


def test_check_port_available_reports_owner(monkeypatch):
    class FakeSocket:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return None

        def settimeout(self, timeout):
            return None

        def connect_ex(self, address):
            return 0

    monkeypatch.setattr(doctor.socket, "socket", lambda *args, **kwargs: FakeSocket())
    monkeypatch.setattr(doctor, "find_port_owner", lambda port: {"pid": "4321", "command": "python app"})

    check = doctor.check_port_available(7860)

    assert not check.ok
    assert "PID 4321" in check.detail
    assert "python app" in check.detail


def test_safe_provider_status_reports_exception():
    import asyncio

    status = asyncio.run(doctor.safe_provider_status("asr", FailingProvider()))

    assert status["kind"] == "asr"
    assert status["ready"] is False
    assert "boom" in status["message"]


def test_check_provider_statuses_continues_after_provider_failure():
    import asyncio
    from types import SimpleNamespace

    providers = SimpleNamespace(
        vad=HealthyProvider(),
        asr=FailingProvider(),
        llm=HealthyProvider(),
        tts=HealthyProvider(),
    )

    statuses = asyncio.run(doctor.check_provider_statuses(providers))

    assert len(statuses) == 4
    assert statuses[0]["ready"] is True
    assert statuses[1]["ready"] is False
    assert "boom" in statuses[1]["message"]


def test_collect_checks_continues_after_check_failure():
    checks = doctor.collect_checks(
        [
            ("first", lambda: doctor.Check("first", True, "ok")),
            ("second", lambda: (_ for _ in ()).throw(RuntimeError("broken"))),
            ("third", lambda: doctor.Check("third", True, "still ran")),
        ]
    )

    assert [check.name for check in checks] == ["first", "second", "third"]
    assert checks[1].ok is False
    assert "broken" in checks[1].detail
