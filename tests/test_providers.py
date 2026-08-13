"""Tests for providers.py: AIWorker's subprocess/network plumbing.

No real `claude` binary, Anthropic API or HTTP server is touched — every
external boundary (shutil.which, subprocess.Popen, anthropic.Anthropic,
urllib.request.urlopen) is faked so the tests exercise AIWorker's own logic
(command construction, chunk assembly, cancellation, error mapping) rather
than the network/process.

AIWorker.run() is called directly (never .start()) so it executes
synchronously in the test thread — Qt signal connections still fire
immediately since there's no cross-thread queueing involved.
"""

from __future__ import annotations

import io
import json
import subprocess
import urllib.error
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import pytest

import kortalk.providers as providers_mod
from kortalk.config import Provider
from kortalk.providers import (
    AIWorker,
    _anthropic_messages,
    _flatten_messages,
    _openai_messages,
    _resolve_claude_cli_attachments,
    _tool_permission_args,
    check_provider,
)


@pytest.fixture(autouse=True)
def _clean_active_workers():
    """AIWorker.__init__ registers itself in the module-level _ACTIVE_WORKERS
    set and only unregisters via the `finished` signal, which real QThread
    machinery emits — never fired here since run() is called directly
    instead of start(). Without this, every worker built in a test would
    leak into the next one."""
    yield
    providers_mod._ACTIVE_WORKERS.clear()


def _make_worker(provider: Provider, messages=None, timeout: int = 5, **kwargs) -> AIWorker:
    messages = messages or [{"role": "user", "content": "hello"}]
    return AIWorker(provider, messages, timeout, **kwargs)


def _http_response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return httpx.Response(status_code, request=request)


# -- check_provider -----------------------------------------------------------

def test_check_provider_claude_cli_found(monkeypatch):
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    ok, _ = check_provider(Provider(id="c", name="c", type="claude-cli"))
    assert ok


def test_check_provider_claude_cli_missing(monkeypatch):
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: None)
    ok, _ = check_provider(Provider(id="c", name="c", type="claude-cli"))
    assert not ok


def test_check_provider_anthropic_needs_key():
    assert not check_provider(Provider(id="a", name="a", type="anthropic"))[0]
    assert check_provider(Provider(id="a", name="a", type="anthropic", api_key="sk-x"))[0]


def test_check_provider_openai_needs_base_url_then_model_then_key():
    p = Provider(id="o", name="o", type="openai")
    assert not check_provider(p)[0]
    p.base_url = "https://api.openai.com/v1"
    assert not check_provider(p)[0]  # no model yet
    p.model = "gpt-4o"
    assert not check_provider(p)[0]  # remote host needs a key
    p.api_key = "sk-x"
    assert check_provider(p)[0]


def test_check_provider_openai_localhost_needs_no_key():
    p = Provider(id="o", name="o", type="openai",
                 base_url="http://localhost:11434/v1", model="llama3")
    assert check_provider(p)[0]


def test_check_provider_unknown_type():
    assert not check_provider(Provider(id="x", name="x", type="bogus"))[0]


# -- pure helpers --------------------------------------------------------------

def test_tool_permission_args_defaults_web_search_on_local_commands_off():
    args = _tool_permission_args(web_search=True, local_commands=False)
    assert args == ["--allowedTools=WebSearch,WebFetch", "--disallowedTools=Bash,Edit,Write"]


def test_tool_permission_args_everything_off():
    args = _tool_permission_args(web_search=False, local_commands=False)
    assert args == ["--disallowedTools=WebSearch,WebFetch,Bash,Edit,Write"]


def test_tool_permission_args_everything_on():
    args = _tool_permission_args(web_search=True, local_commands=True)
    assert args == ["--allowedTools=WebSearch,WebFetch,Bash,Edit,Write"]


def test_flatten_single_message_passthrough():
    assert _flatten_messages([{"role": "user", "content": "hi"}]) == "hi"


def test_flatten_multi_turn_joins_speakers():
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": "how are you"},
    ]
    assert _flatten_messages(messages) == (
        "Human: hi\n\nAssistant: hello\n\nHuman: how are you\n\nAssistant:"
    )


# -- attachments: per-provider message building -----------------------------------

_IMAGE_ATT = {"kind": "image", "name": "pic.png", "mime": "image/png", "data": "QUFB"}
_FILE_ATT = {"kind": "file", "name": "notes.txt", "mime": "", "data": "line one"}


def test_resolve_claude_cli_attachments_no_attachments_is_a_passthrough():
    messages = [{"role": "user", "content": "hi"}]
    resolved, image_dir = _resolve_claude_cli_attachments(messages)
    assert resolved == messages
    assert image_dir is None


def test_resolve_claude_cli_attachments_writes_image_and_appends_marker(tmp_path):
    messages = [{"role": "user", "content": "what is this",
                 "attachments": [_IMAGE_ATT]}]
    resolved, image_dir = _resolve_claude_cli_attachments(messages)
    try:
        assert image_dir is not None
        written = image_dir / "0_pic.png"
        assert written.exists()
        assert written.read_bytes() == b"AAA"  # base64 "QUFB"
        assert resolved[0]["content"] == (
            'what is this\n\n(There is an image attached at "0_pic.png" in the '
            "current directory. Read that file before answering.)")
        assert "attachments" not in resolved[0]  # never passed through as-is
    finally:
        import shutil
        shutil.rmtree(image_dir, ignore_errors=True)


def test_resolve_claude_cli_attachments_text_file_needs_no_temp_dir():
    messages = [{"role": "user", "content": "explain", "attachments": [_FILE_ATT]}]
    resolved, image_dir = _resolve_claude_cli_attachments(messages)
    assert image_dir is None  # nothing to materialize on disk
    assert "line one" in resolved[0]["content"]
    assert "notes.txt" in resolved[0]["content"]


def test_resolve_claude_cli_attachments_numbers_images_across_whole_conversation(tmp_path):
    # Two different messages each attaching a same-named image must not
    # collide in the one shared temp directory.
    messages = [
        {"role": "user", "content": "first", "attachments": [_IMAGE_ATT]},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "second", "attachments": [_IMAGE_ATT]},
    ]
    resolved, image_dir = _resolve_claude_cli_attachments(messages)
    try:
        assert (image_dir / "0_pic.png").exists()
        assert (image_dir / "1_pic.png").exists()
        assert resolved[0]["content"] == (
            'first\n\n(There is an image attached at "0_pic.png" in the '
            "current directory. Read that file before answering.)")
        assert resolved[2]["content"] == (
            'second\n\n(There is an image attached at "1_pic.png" in the '
            "current directory. Read that file before answering.)")
    finally:
        import shutil
        shutil.rmtree(image_dir, ignore_errors=True)


def test_anthropic_messages_passthrough_for_plain_text():
    messages = [{"role": "user", "content": "hi"}]
    assert _anthropic_messages(messages) == messages


def test_anthropic_messages_builds_image_and_text_blocks():
    messages = [{"role": "user", "content": "what is this", "attachments": [_IMAGE_ATT]}]
    result = _anthropic_messages(messages)
    assert result == [{
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": "QUFB"}},
            {"type": "text", "text": "what is this"},
        ],
    }]


def test_anthropic_messages_folds_text_attachment_into_text_block():
    messages = [{"role": "user", "content": "explain", "attachments": [_FILE_ATT]}]
    result = _anthropic_messages(messages)
    assert result[0]["content"] == [
        {"type": "text", "text": "explain\n\n--- notes.txt ---\nline one\n--- end notes.txt ---"}
    ]


def test_openai_messages_builds_image_url_block():
    messages = [{"role": "user", "content": "what is this", "attachments": [_IMAGE_ATT]}]
    result = _openai_messages(messages)
    assert result == [{
        "role": "user",
        "content": [
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUFB"}},
            {"type": "text", "text": "what is this"},
        ],
    }]


def test_openai_messages_passthrough_for_plain_text():
    messages = [{"role": "assistant", "content": "hello"}]
    assert _openai_messages(messages) == messages


# -- Claude Code CLI -----------------------------------------------------------

def test_claude_cli_missing_binary_reports_failure(monkeypatch):
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: None)
    worker = _make_worker(Provider(id="c", name="c", type="claude-cli"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert len(failures) == 1
    assert "not found in PATH" in failures[0]


def test_claude_cli_builds_expected_argv_list(monkeypatch):
    """Guards against shell-injection regressions: the command must stay a
    list of argv tokens (subprocess.Popen with shell=False), never a
    string handed to a shell."""
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    captured = {}

    class _FakeProcess:
        pid = 111
        returncode = 0

        def communicate(self, timeout=None):
            return "answer", ""

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(providers_mod.subprocess, "Popen", _fake_popen)

    provider = Provider(id="c", name="c", type="claude-cli", model="opus",
                         extra_args=["--foo"])
    worker = _make_worker(provider, messages=[{"role": "user", "content": "hi there"}])
    results = []
    worker.finished_ok.connect(results.append)
    worker.run()

    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert cmd[0] == "/usr/bin/claude"
    # -p, "--" and the prompt must be the trailing three argv elements, in
    # that order — see test_claude_cli_prompt_starting_with_dash_is_not_
    # parsed_as_an_option for why the prompt can never lead anything else.
    assert cmd[-3:] == ["-p", "--", "hi there"]
    assert "--model" in cmd and "opus" in cmd
    assert "--foo" in cmd
    assert captured["kwargs"]["preexec_fn"] is providers_mod.os.setpgrp
    assert results == ["answer"]


def test_claude_cli_prompt_starting_with_dash_is_not_parsed_as_an_option(monkeypatch):
    # Regression: claude's CLI parser (Commander.js) treats any positional
    # argument starting with "-" as an attempted option rather than a
    # value — a copied YAML document (starting with "---") or anything
    # else beginning with a dash used to blow up with "error: unknown
    # option '---...'" instead of being read as the prompt. "--" must
    # immediately precede the prompt, and nothing may follow it.
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    captured = {}

    class _FakeProcess:
        pid = 111
        returncode = 0

        def communicate(self, timeout=None):
            return "answer", ""

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess()

    monkeypatch.setattr(providers_mod.subprocess, "Popen", _fake_popen)

    provider = Provider(id="c", name="c", type="claude-cli", model="opus")
    dashy_prompt = "---\nfoo: bar\n"
    worker = _make_worker(provider, messages=[{"role": "user", "content": dashy_prompt}])
    worker.run()

    cmd = captured["cmd"]
    dash_index = cmd.index("--")
    assert cmd[dash_index + 1] == dashy_prompt
    assert dash_index + 1 == len(cmd) - 1  # nothing after the prompt to swallow


def test_claude_cli_runs_with_cwd_set_to_image_dir_and_cleans_up(monkeypatch):
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    captured = {}

    class _FakeProcess:
        pid = 111
        returncode = 0

        def communicate(self, timeout=None):
            return "it's a red square", ""

    def _fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        # the image must already be on disk by the time the process "runs"
        assert captured["cwd"] is not None
        assert (providers_mod.Path(captured["cwd"]) / "0_pic.png").exists()
        return _FakeProcess()

    monkeypatch.setattr(providers_mod.subprocess, "Popen", _fake_popen)

    provider = Provider(id="c", name="c", type="claude-cli")
    worker = _make_worker(provider, messages=[
        {"role": "user", "content": "what color", "attachments": [_IMAGE_ATT]},
    ])
    results = []
    worker.finished_ok.connect(results.append)
    worker.run()

    assert results == ["it's a red square"]
    assert '"0_pic.png"' in captured["cmd"][-1]
    assert "Read that file" in captured["cmd"][-1]
    assert not providers_mod.Path(captured["cwd"]).exists()  # cleaned up after the run


def test_claude_cli_without_attachments_keeps_cwd_unset(monkeypatch):
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: "/usr/bin/claude")
    captured = {}

    class _FakeProcess:
        pid = 111
        returncode = 0

        def communicate(self, timeout=None):
            return "answer", ""

    def _fake_popen(cmd, **kwargs):
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProcess()

    monkeypatch.setattr(providers_mod.subprocess, "Popen", _fake_popen)

    worker = _make_worker(Provider(id="c", name="c", type="claude-cli"))
    worker.run()

    assert captured["cwd"] is None


def test_claude_cli_nonzero_exit_reports_stderr(monkeypatch):
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: "/usr/bin/claude")

    class _FakeProcess:
        pid = 111
        returncode = 1

        def communicate(self, timeout=None):
            return "", "boom"

    monkeypatch.setattr(providers_mod.subprocess, "Popen", lambda *a, **k: _FakeProcess())

    worker = _make_worker(Provider(id="c", name="c", type="claude-cli"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "boom" in failures[0]


def test_claude_cli_timeout_kills_process_group_and_reports(monkeypatch):
    monkeypatch.setattr(providers_mod.shutil, "which", lambda _name: "/usr/bin/claude")

    class _FakeProcess:
        pid = 4321

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd="claude", timeout=timeout)

    monkeypatch.setattr(providers_mod.subprocess, "Popen", lambda *a, **k: _FakeProcess())
    killed = []
    monkeypatch.setattr(providers_mod.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(providers_mod.os, "killpg", lambda pgid, sig: killed.append((pgid, sig)))

    worker = _make_worker(Provider(id="c", name="c", type="claude-cli"), timeout=5)
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "did not respond within" in failures[0]
    assert killed == [(4321, providers_mod.signal.SIGKILL)]


def test_stop_kills_process_group_and_closes_stream():
    worker = _make_worker(Provider(id="c", name="c", type="claude-cli"))
    fake_process = MagicMock()
    fake_process.pid = 999
    worker._process = fake_process
    fake_stream = MagicMock()
    worker._stream = fake_stream

    worker.stop()

    assert worker._cancelled is True
    fake_stream.close.assert_called_once()


# -- Anthropic API --------------------------------------------------------------

class _FakeAnthropicStream:
    def __init__(self, chunks, stop_reason="end_turn"):
        self._chunks = chunks
        self._stop_reason = stop_reason

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    @property
    def text_stream(self):
        return iter(self._chunks)

    def get_final_message(self):
        return SimpleNamespace(stop_reason=self._stop_reason)


class _FakeAnthropicClient:
    def __init__(self, stream_obj):
        self.messages = SimpleNamespace(stream=lambda **kw: stream_obj)


def test_anthropic_missing_key_reports_failure():
    worker = _make_worker(Provider(id="a", name="a", type="anthropic"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "API key is not set" in failures[0]


def test_anthropic_success_streams_chunks_and_joins_final_text(monkeypatch):
    import anthropic

    fake_stream = _FakeAnthropicStream(["Hel", "lo"])
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _FakeAnthropicClient(fake_stream))

    worker = _make_worker(Provider(id="a", name="a", type="anthropic", api_key="sk-x"))
    chunks, results = [], []
    worker.chunk.connect(chunks.append)
    worker.finished_ok.connect(results.append)
    worker.run()

    assert chunks == ["Hel", "lo"]
    assert results == ["Hello"]


def test_anthropic_run_sends_image_block_for_attached_message(monkeypatch):
    import anthropic

    captured = {}

    def _fake_stream(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _FakeAnthropicStream(["ok"])

    monkeypatch.setattr(anthropic, "Anthropic",
                        lambda **kw: SimpleNamespace(messages=SimpleNamespace(stream=_fake_stream)))

    worker = _make_worker(Provider(id="a", name="a", type="anthropic", api_key="sk-x"),
                          messages=[{"role": "user", "content": "what is this",
                                     "attachments": [_IMAGE_ATT]}])
    worker.run()

    sent = captured["messages"][0]
    assert sent["role"] == "user"
    assert {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": "QUFB"}} in sent["content"]


def test_anthropic_refusal_reports_failure_not_partial_text(monkeypatch):
    import anthropic

    fake_stream = _FakeAnthropicStream(["par", "tial"], stop_reason="refusal")
    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _FakeAnthropicClient(fake_stream))

    worker = _make_worker(Provider(id="a", name="a", type="anthropic", api_key="sk-x"))
    failures, results = [], []
    worker.failed.connect(failures.append)
    worker.finished_ok.connect(results.append)
    worker.run()

    assert results == []
    assert failures and "declined" in failures[0]


def test_anthropic_authentication_error_reports_friendly_message(monkeypatch):
    import anthropic

    response = _http_response(401)

    class _RaisingClient:
        messages = SimpleNamespace(stream=MagicMock(
            side_effect=anthropic.AuthenticationError("bad key", response=response, body=None)))

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _RaisingClient())
    worker = _make_worker(Provider(id="a", name="a", type="anthropic", api_key="sk-bad"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == ["Invalid Anthropic API key."]


def test_anthropic_rate_limit_error_reports_friendly_message(monkeypatch):
    import anthropic

    response = _http_response(429)

    class _RaisingClient:
        messages = SimpleNamespace(stream=MagicMock(
            side_effect=anthropic.RateLimitError("slow down", response=response, body=None)))

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _RaisingClient())
    worker = _make_worker(Provider(id="a", name="a", type="anthropic", api_key="sk-x"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "rate limit" in failures[0].lower()


def test_anthropic_connection_error_is_swallowed_when_cancelled(monkeypatch):
    import anthropic

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")

    class _RaisingClient:
        messages = SimpleNamespace(stream=MagicMock(
            side_effect=anthropic.APIConnectionError(message="closed", request=request)))

    monkeypatch.setattr(anthropic, "Anthropic", lambda **kw: _RaisingClient())
    worker = _make_worker(Provider(id="a", name="a", type="anthropic", api_key="sk-x"))
    worker._cancelled = True
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == []  # a cancel-triggered disconnect must not surface as an error


# -- OpenAI-compatible APIs ------------------------------------------------------

class _FakeHTTPResponse:
    def __init__(self, lines: list[bytes]):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def __iter__(self):
        return iter(self._lines)


def test_openai_compatible_success_streams_delta_chunks(monkeypatch):
    lines = [
        b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
        b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
        b"data: [DONE]\n",
    ]
    monkeypatch.setattr(providers_mod.urllib.request, "urlopen",
                        lambda *a, **k: _FakeHTTPResponse(lines))

    worker = _make_worker(Provider(id="o", name="o", type="openai",
                                    base_url="http://localhost:11434/v1", model="llama3"))
    chunks, results = [], []
    worker.chunk.connect(chunks.append)
    worker.finished_ok.connect(results.append)
    worker.run()

    assert chunks == ["Hel", "lo"]
    assert results == ["Hello"]


def test_openai_compatible_sends_image_url_block_for_attached_message(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=None):
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse([b"data: [DONE]\n"])

    monkeypatch.setattr(providers_mod.urllib.request, "urlopen", _fake_urlopen)

    worker = _make_worker(
        Provider(id="o", name="o", type="openai",
                base_url="http://localhost:11434/v1", model="llama3"),
        messages=[{"role": "user", "content": "what is this", "attachments": [_IMAGE_ATT]}],
    )
    worker.run()

    sent = captured["body"]["messages"][0]
    assert {"type": "image_url", "image_url": {"url": "data:image/png;base64,QUFB"}} in (
        sent["content"])


def test_openai_compatible_missing_base_url():
    worker = _make_worker(Provider(id="o", name="o", type="openai", model="llama3"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "base URL is not set" in failures[0]


def test_openai_compatible_missing_model():
    worker = _make_worker(Provider(id="o", name="o", type="openai",
                                    base_url="http://localhost:11434/v1"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "Model is not set" in failures[0]


def test_openai_compatible_missing_key_for_remote_host():
    worker = _make_worker(Provider(id="o", name="o", type="openai",
                                    base_url="https://api.openai.com/v1", model="gpt-4o"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "API key is not set" in failures[0]


def test_openai_compatible_sends_bearer_header_when_key_set(monkeypatch):
    captured = {}

    def _fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _FakeHTTPResponse([b"data: [DONE]\n"])

    monkeypatch.setattr(providers_mod.urllib.request, "urlopen", _fake_urlopen)
    worker = _make_worker(Provider(id="o", name="o", type="openai",
                                    base_url="https://api.openai.com/v1", model="gpt-4o",
                                    api_key="sk-x"))
    worker.run()
    assert captured["request"].get_header("Authorization") == "Bearer sk-x"
    assert captured["request"].full_url == "https://api.openai.com/v1/chat/completions"


def test_openai_compatible_http_error_reports_server_message(monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.HTTPError(
            url="http://x", code=401, msg="Unauthorized", hdrs=None,
            fp=io.BytesIO(json.dumps({"error": {"message": "bad key"}}).encode()),
        )

    monkeypatch.setattr(providers_mod.urllib.request, "urlopen", _raise)
    worker = _make_worker(Provider(id="o", name="o", type="openai",
                                    base_url="http://localhost:11434/v1", model="llama3"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "bad key" in failures[0]


def test_openai_compatible_connection_error_hints_ollama(monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.URLError("Connection refused")

    monkeypatch.setattr(providers_mod.urllib.request, "urlopen", _raise)
    worker = _make_worker(Provider(id="o", name="o", type="openai",
                                    base_url="http://localhost:11434/v1", model="llama3"))
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures and "ollama serve" in failures[0].lower()


def test_openai_compatible_connection_error_is_swallowed_when_cancelled(monkeypatch):
    def _raise(*a, **k):
        raise urllib.error.URLError("closed by stop()")

    monkeypatch.setattr(providers_mod.urllib.request, "urlopen", _raise)
    worker = _make_worker(Provider(id="o", name="o", type="openai",
                                    base_url="http://localhost:11434/v1", model="llama3"))
    worker._cancelled = True
    failures = []
    worker.failed.connect(failures.append)
    worker.run()
    assert failures == []
