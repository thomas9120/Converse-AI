from pathlib import Path
from tempfile import TemporaryFile
from types import SimpleNamespace

import pytest

from conversational_harness import launch
from conversational_harness.doctor import Check


class FakeProcess:
    def __init__(self, *, wait_result=0, interrupt=False):
        self.wait_result = wait_result
        self.interrupt = interrupt
        self.wait_calls = 0
        self.terminated = False
        self.killed = False

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.interrupt and self.wait_calls == 1:
            raise KeyboardInterrupt
        return self.wait_result

    def poll(self):
        return None if not self.terminated and not self.killed else self.wait_result

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True


def test_list_profiles_includes_project_profiles():
    profiles = launch.list_profiles()

    assert any(profile.name == "mock-local.json" for profile in profiles)
    assert all(profile.name != "tts-presets.json" for profile in profiles)
    assert profiles == sorted(profiles)


def test_choose_profile_uses_explicit_profile():
    profile = launch.choose_profile("profiles/mock-local.json", launch.list_profiles(), interactive=True)

    assert profile.name == "mock-local.json"


def test_maybe_open_browser_skips_when_disabled(monkeypatch):
    opened = []
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: opened.append(url))
    args = SimpleNamespace(no_browser=True, open_browser=False)

    launch.maybe_open_browser("http://127.0.0.1:7860", args, interactive=True)

    assert opened == []


def test_maybe_open_browser_opens_when_requested(monkeypatch):
    opened = []
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: opened.append(url))
    args = SimpleNamespace(no_browser=False, open_browser=True)

    launch.maybe_open_browser("http://127.0.0.1:7860", args, interactive=False)

    assert opened == ["http://127.0.0.1:7860"]


def test_parse_args_accepts_cloudflare_tunnel():
    args = launch.parse_args(["--cloudflare-tunnel"])

    assert args.cloudflare_tunnel is True
    assert args.no_cloudflare_tunnel is False
    assert args.cloudflared_url == launch.DEFAULT_CLOUDFLARED_URL


def test_choose_cloudflare_tunnel_uses_explicit_flag():
    args = launch.parse_args(["--cloudflare-tunnel"])

    assert launch.choose_cloudflare_tunnel(args, interactive=False) is True


def test_choose_cloudflare_tunnel_skips_when_disabled(monkeypatch):
    prompted = []
    args = launch.parse_args(["--no-cloudflare-tunnel"])
    monkeypatch.setattr("builtins.input", lambda prompt: prompted.append(prompt) or "y")

    assert launch.choose_cloudflare_tunnel(args, interactive=True) is False
    assert prompted == []


def test_choose_cloudflare_tunnel_prompts_interactively(monkeypatch):
    args = launch.parse_args([])
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    assert launch.choose_cloudflare_tunnel(args, interactive=True) is True


def test_main_list_profiles_exits_without_starting(monkeypatch, capsys):
    monkeypatch.setattr(launch, "start_server", lambda port, env: (_ for _ in ()).throw(AssertionError("started")))

    exit_code = launch.main(["--list-profiles"])

    assert exit_code == 0
    assert "profiles/mock-local.json" in capsys.readouterr().out


def test_main_stops_on_fatal_preflight(monkeypatch):
    monkeypatch.setattr(
        launch,
        "run_launch_checks",
        lambda config, port, skip_port_check=False: [Check("Harness port", False, "busy")],
    )
    monkeypatch.setattr(launch, "start_server", lambda port, env: (_ for _ in ()).throw(AssertionError("started")))

    exit_code = launch.main(["--profile", "profiles/mock-local.json", "--no-prompt", "--no-browser"])

    assert exit_code == 1


def test_main_noninteractive_starts_server_with_expected_env(monkeypatch):
    calls = {}

    def fake_start_server(port, env):
        calls["port"] = port
        calls["env"] = env
        return FakeProcess()

    monkeypatch.setattr(launch, "run_launch_checks", lambda config, port, skip_port_check=False: [])
    monkeypatch.setattr(launch, "start_server", fake_start_server)
    monkeypatch.setattr(launch, "wait_for_server", lambda url: True)
    monkeypatch.setattr(launch, "maybe_open_browser", lambda url, args, interactive: calls.setdefault("browser", False))

    exit_code = launch.main(
        ["--profile", "profiles/mock-local.json", "--port", "7861", "--no-prompt", "--no-browser"]
    )

    assert exit_code == 0
    assert calls["port"] == 7861
    assert Path(calls["env"]["HARNESS_PROFILE"]).name == "mock-local.json"
    assert calls["env"]["HARNESS_PORT"] == "7861"
    assert calls["browser"] is False


def test_main_starts_cloudflare_tunnel_after_server_ready(monkeypatch, capsys):
    calls = []
    server = FakeProcess()
    tunnel = FakeProcess()

    monkeypatch.setattr(launch, "run_launch_checks", lambda config, port, skip_port_check=False: [])
    monkeypatch.setattr(launch, "start_server", lambda port, env: server)
    monkeypatch.setattr(launch, "wait_for_server", lambda url: calls.append("ready") or True)
    monkeypatch.setattr(launch, "maybe_open_browser", lambda url, args, interactive: calls.append("browser"))
    monkeypatch.setattr(
        launch,
        "start_cloudflare_tunnel",
        lambda port, cloudflared_url: calls.append(("tunnel", port, cloudflared_url)) or tunnel,
    )

    exit_code = launch.main(
        ["--profile", "profiles/mock-local.json", "--no-prompt", "--no-browser", "--cloudflare-tunnel"]
    )

    assert exit_code == 0
    assert calls == ["ready", ("tunnel", 7860, launch.DEFAULT_CLOUDFLARED_URL), "browser"]
    assert tunnel.terminated is True
    assert "Cloudflare tunnel: yes" in capsys.readouterr().out


def test_main_starts_cloudflare_tunnel_after_readiness_timeout_when_server_still_running(monkeypatch, capsys):
    calls = []
    tunnel = FakeProcess()

    monkeypatch.setattr(launch, "run_launch_checks", lambda config, port, skip_port_check=False: [])
    monkeypatch.setattr(launch, "start_server", lambda port, env: FakeProcess())
    monkeypatch.setattr(launch, "wait_for_server", lambda url: False)
    monkeypatch.setattr(launch, "maybe_open_browser", lambda url, args, interactive: calls.append("browser"))
    monkeypatch.setattr(
        launch,
        "start_cloudflare_tunnel",
        lambda port, cloudflared_url: calls.append(("tunnel", port, cloudflared_url)) or tunnel,
    )

    exit_code = launch.main(
        ["--profile", "profiles/mock-local.json", "--no-prompt", "--no-browser", "--cloudflare-tunnel"]
    )

    assert exit_code == 0
    assert calls == [("tunnel", 7860, launch.DEFAULT_CLOUDFLARED_URL)]
    captured = capsys.readouterr()
    assert "Cloudflare tunnel: yes" in captured.out
    assert "local app may still be warming up" in captured.err
    assert tunnel.terminated is True


def test_main_does_not_start_cloudflare_tunnel_after_readiness_timeout_when_server_exited(monkeypatch):
    class ExitedProcess(FakeProcess):
        def poll(self):
            return 1

    monkeypatch.setattr(launch, "run_launch_checks", lambda config, port, skip_port_check=False: [])
    monkeypatch.setattr(launch, "start_server", lambda port, env: ExitedProcess(wait_result=1))
    monkeypatch.setattr(launch, "wait_for_server", lambda url: False)
    monkeypatch.setattr(
        launch,
        "start_cloudflare_tunnel",
        lambda port, cloudflared_url: (_ for _ in ()).throw(AssertionError("tunnel started")),
    )

    exit_code = launch.main(
        ["--profile", "profiles/mock-local.json", "--no-prompt", "--no-browser", "--cloudflare-tunnel"]
    )

    assert exit_code == 1


def test_main_keyboard_interrupt_stops_server_and_tunnel(monkeypatch):
    server = FakeProcess(interrupt=True)
    tunnel = FakeProcess()

    monkeypatch.setattr(launch, "run_launch_checks", lambda config, port, skip_port_check=False: [])
    monkeypatch.setattr(launch, "start_server", lambda port, env: server)
    monkeypatch.setattr(launch, "wait_for_server", lambda url: True)
    monkeypatch.setattr(launch, "maybe_open_browser", lambda url, args, interactive: None)
    monkeypatch.setattr(launch, "start_cloudflare_tunnel", lambda port, cloudflared_url: tunnel)

    exit_code = launch.main(
        ["--profile", "profiles/mock-local.json", "--no-prompt", "--no-browser", "--cloudflare-tunnel"]
    )

    assert exit_code == 130
    assert server.terminated is True
    assert tunnel.terminated is True


def test_monitor_cloudflared_output_prints_public_url(capsys):
    with TemporaryFile(mode="w+") as output:
        output.write("INF Requesting new quick Tunnel on trycloudflare.com...\n")
        output.write("INF https://quiet-lake-123.trycloudflare.com\n")
        output.seek(0)
        process = SimpleNamespace(stdout=output)

        thread = launch.monitor_cloudflared_output(process)
        thread.join(timeout=1)

    captured = capsys.readouterr().out
    assert "INF https://quiet-lake-123.trycloudflare.com" in captured
    assert "Cloudflare tunnel URL: https://quiet-lake-123.trycloudflare.com" in captured


def test_start_cloudflare_tunnel_captures_output(monkeypatch, tmp_path):
    calls = {}
    cloudflared = tmp_path / "cloudflared.exe"
    fake_process = SimpleNamespace(stdout=None)

    def fake_popen(command, **kwargs):
        calls["command"] = command
        calls["kwargs"] = kwargs
        return fake_process

    monkeypatch.setattr(launch, "ensure_cloudflared", lambda cloudflared_url: cloudflared)
    monkeypatch.setattr(launch.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(launch, "monitor_cloudflared_output", lambda process: calls.setdefault("monitored", process))

    process = launch.start_cloudflare_tunnel(7861, launch.DEFAULT_CLOUDFLARED_URL)

    assert process is fake_process
    assert calls["command"] == [str(cloudflared), "tunnel", "--url", "http://localhost:7861"]
    assert calls["kwargs"]["stdout"] == launch.subprocess.PIPE
    assert calls["kwargs"]["stderr"] == launch.subprocess.STDOUT
    assert calls["kwargs"]["text"] is True
    assert calls["monitored"] is fake_process


def test_ensure_cloudflared_downloads_missing_binary_without_real_network(monkeypatch, tmp_path):
    tools_dir = tmp_path / "tools"
    downloaded = []

    monkeypatch.setattr(launch, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launch.os, "name", "nt")

    def fake_urlretrieve(url, path):
        downloaded.append((url, path))
        path.write_bytes(b"0" * launch.CLOUDFLARED_MIN_BYTES)

    monkeypatch.setattr(launch.urllib.request, "urlretrieve", fake_urlretrieve)

    cloudflared = launch.ensure_cloudflared("https://example.test/cloudflared.exe")

    assert cloudflared == tools_dir / "cloudflared.exe"
    assert downloaded == [("https://example.test/cloudflared.exe", tools_dir / "cloudflared.exe")]


def test_ensure_cloudflared_rejects_too_small_download(monkeypatch, tmp_path):
    monkeypatch.setattr(launch, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(launch.os, "name", "nt")
    monkeypatch.setattr(
        launch.urllib.request,
        "urlretrieve",
        lambda url, path: path.write_bytes(b"too small"),
    )

    with pytest.raises(RuntimeError, match="too small"):
        launch.ensure_cloudflared("https://example.test/cloudflared.exe")

    assert not (tmp_path / "tools" / "cloudflared.exe").exists()
