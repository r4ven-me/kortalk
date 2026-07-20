"""AI providers: Claude Code CLI, Anthropic API, OpenAI-compatible APIs.

Every request runs in an AIWorker (QThread) and streams text via signals:
chunk (delta), finished_ok (full text), failed (error message).
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import urllib.error
import urllib.request

from PySide6.QtCore import QThread, Signal

from .config import Provider
from .i18n import tr

log = logging.getLogger(__name__)


def check_provider(p: Provider) -> tuple[bool, str]:
    """Static availability check for a provider — the same checks behind
    `kortalk --check`, reused by the settings dialog. No network calls: it
    only checks what AIWorker itself would refuse to start without."""
    if p.type == "claude-cli":
        if shutil.which("claude"):
            return True, tr("claude found in PATH.")
        return False, tr("claude not found in PATH — install Claude Code CLI.")
    if p.type == "anthropic":
        if not p.api_key:
            return False, tr("API key is not set.")
        return True, tr("API key is set.")
    if p.type == "openai":
        if not p.base_url:
            return False, tr("Base URL is not set.")
        if not p.model:
            return False, tr("Model is not set.")
        if p.needs_api_key() and not p.api_key:
            return False, tr("API key is not set.")
        return True, tr("Base URL, model and key are set.")
    return False, tr("Unknown provider type.")


# Live workers. A QThread must not be destroyed while its thread is running
# (Qt aborts -> core dump), so workers have NO Qt parent: a window may close
# and be deleted at any moment while the worker lives out its life here and
# removes itself once the thread finishes.
_ACTIVE_WORKERS: set[AIWorker] = set()


def shutdown_workers(wait_ms: int = 500) -> None:
    """Stops all live workers before the application exits.

    stop() closes the worker's network stream/process, so a blocked read is
    interrupted right away and wait almost never hits the timeout.
    """
    for worker in list(_ACTIVE_WORKERS):
        worker.stop()
    for worker in list(_ACTIVE_WORKERS):
        if not worker.wait(wait_ms):
            worker.terminate()  # last resort on exit: the thread hung on I/O
            worker.wait(500)


class AIWorker(QThread):
    chunk = Signal(str)          # next text delta (streaming providers only)
    finished_ok = Signal(str)    # full final text
    failed = Signal(str)         # human-readable error

    def __init__(self, provider: Provider, prompt: str, timeout: int,
                 max_tokens: int = 64000):
        super().__init__()  # no Qt parent, see _ACTIVE_WORKERS
        self.provider = provider
        self.prompt = prompt
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._cancelled = False
        self._process: subprocess.Popen | None = None
        self._stream = None  # active network stream (anthropic MessageStream / HTTPResponse)
        _ACTIVE_WORKERS.add(self)
        self.finished.connect(self._on_thread_finished)
        self.finished_ok.connect(
            lambda text: log.info("response %s: %d chars", provider.id, len(text)))
        self.failed.connect(
            lambda message: log.warning("failure %s: %s", provider.id, message))

    def _on_thread_finished(self) -> None:
        _ACTIVE_WORKERS.discard(self)
        self.deleteLater()

    def stop(self) -> None:
        self._cancelled = True
        if self._process is not None:
            try:
                self._process.kill()
            except OSError:
                pass
        stream = self._stream
        if stream is not None:
            try:
                stream.close()  # wakes a thread blocked on a network read
            except Exception:  # noqa: BLE001 — closing from another thread
                pass

    def run(self) -> None:
        log.info("request: provider=%s (%s), model=%s, %d chars of prompt",
                 self.provider.id, self.provider.type,
                 self.provider.model or "<default>", len(self.prompt))
        try:
            if self.provider.type == "claude-cli":
                self._run_claude_cli()
            elif self.provider.type == "anthropic":
                self._run_anthropic()
            elif self.provider.type == "openai":
                self._run_openai_compatible()
            else:
                self.failed.emit(tr("Unknown provider type: {type}")
                                 .format(type=self.provider.type))
        except Exception as exc:  # noqa: BLE001 — the thread must not die silently
            if not self._cancelled:
                log.exception("unexpected worker error")
                self.failed.emit(tr("Unexpected error: {error}").format(error=exc))

    # -- Claude Code CLI ------------------------------------------------------

    def _run_claude_cli(self) -> None:
        claude_bin = shutil.which("claude")
        if not claude_bin:
            self.failed.emit(tr(
                "Claude Code CLI (`claude`) not found in PATH.\n"
                "Install: https://docs.claude.com"
            ))
            return

        cmd = [claude_bin, "-p", self.prompt]
        if self.provider.model:
            cmd += ["--model", self.provider.model]
        cmd += self.provider.extra_args

        self._process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
        try:
            stdout, stderr = self._process.communicate(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self.failed.emit(tr("claude did not respond within {timeout} s.")
                             .format(timeout=self.timeout))
            return
        if self._cancelled:
            return
        if self._process.returncode != 0:
            self.failed.emit(tr("claude exited with an error:\n{error}")
                             .format(error=(stderr or stdout).strip()))
            return
        self.finished_ok.emit(stdout.strip())

    # -- Anthropic API (official SDK, streaming) ------------------------------

    def _run_anthropic(self) -> None:
        try:
            import anthropic
        except ImportError:
            self.failed.emit(
                tr("The `anthropic` package is not installed (pip install anthropic)."))
            return

        if not self.provider.api_key:
            self.failed.emit(tr("Anthropic API key is not set — open Settings → Providers."))
            return

        client = anthropic.Anthropic(api_key=self.provider.api_key, timeout=float(self.timeout))
        model = self.provider.model or "claude-opus-4-8"
        parts: list[str] = []
        try:
            with client.messages.stream(
                model=model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": self.prompt}],
            ) as stream:
                self._stream = stream
                for text in stream.text_stream:
                    if self._cancelled:
                        return
                    parts.append(text)
                    self.chunk.emit(text)
                final = stream.get_final_message()
            if final.stop_reason == "refusal":
                self.failed.emit(tr("The model declined the request (safety refusal)."))
                return
            self.finished_ok.emit("".join(parts).strip())
        except anthropic.AuthenticationError:
            self.failed.emit(tr("Invalid Anthropic API key."))
        except anthropic.NotFoundError:
            self.failed.emit(tr("Model “{model}” not found — check the name in settings.")
                             .format(model=model))
        except anthropic.RateLimitError:
            self.failed.emit(tr("Anthropic rate limit exceeded — wait and retry."))
        except anthropic.APIStatusError as exc:
            if self._cancelled:
                return
            self.failed.emit(tr("Anthropic API error ({code}): {message}")
                             .format(code=exc.status_code, message=exc.message))
        except anthropic.APIConnectionError:
            if self._cancelled:  # we closed the stream ourselves on cancel/quit
                return
            self.failed.emit(tr("Cannot connect to api.anthropic.com — check your network."))
        finally:
            self._stream = None

    # -- OpenAI-compatible APIs (OpenAI, Ollama, LM Studio, OpenRouter, ...) --

    def _run_openai_compatible(self) -> None:
        base_url = self.provider.base_url.rstrip("/")
        if not base_url:
            self.failed.emit(tr("Provider base URL is not set — open Settings → Providers."))
            return
        if not self.provider.model:
            self.failed.emit(tr(
                "Model is not set — open Settings → Providers.\n"
                "For Ollama: the name of an installed model (see `ollama list`)."
            ))
            return
        if self.provider.needs_api_key() and not self.provider.api_key:
            self.failed.emit(tr("API key is not set — open Settings → Providers."))
            return

        body = {
            "model": self.provider.model,
            "stream": True,
            "messages": [{"role": "user", "content": self.prompt}],
        }
        headers = {"Content-Type": "application/json"}
        if self.provider.api_key:
            headers["Authorization"] = f"Bearer {self.provider.api_key}"

        request = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        parts: list[str] = []
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                self._stream = response
                for raw_line in response:
                    if self._cancelled:
                        return
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                        delta = obj["choices"][0].get("delta", {}).get("content")
                    except (json.JSONDecodeError, LookupError):
                        continue
                    if delta:
                        parts.append(delta)
                        self.chunk.emit(delta)
            self.finished_ok.emit("".join(parts).strip())
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                payload = json.loads(exc.read().decode("utf-8", errors="replace"))
                detail = payload.get("error", {}).get("message", "")
            except Exception:  # noqa: BLE001
                pass
            self.failed.emit(tr("API error ({code}): {message}")
                             .format(code=exc.code, message=detail or exc.reason))
        except urllib.error.URLError as exc:
            if self._cancelled:  # we closed the stream ourselves on cancel/quit
                return
            hint = tr("Is Ollama running? (`ollama serve`)") if "11434" in base_url else ""
            self.failed.emit(
                tr("Cannot connect to {url}: {reason}").format(url=base_url, reason=exc.reason)
                + (f"\n{hint}" if hint else "")
            )
        except TimeoutError:
            self.failed.emit(tr("The provider did not respond within {timeout} s.")
                             .format(timeout=self.timeout))
        finally:
            self._stream = None
