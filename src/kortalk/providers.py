"""Провайдеры ИИ: Claude Code CLI, Anthropic API, OpenAI-совместимые API.

Каждый запрос выполняется в AIWorker (QThread) и стримит текст сигналами:
chunk (дельта), finished_ok (полный текст), failed (сообщение об ошибке).
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


# Живые воркеры. QThread нельзя уничтожать, пока поток работает (Qt делает
# abort → core dump), поэтому воркеры НЕ имеют Qt-родителя: окно может
# закрыться и удалиться в любой момент, а воркер доживает своё здесь и
# удаляется сам по завершении потока.
_ACTIVE_WORKERS: set[AIWorker] = set()


def shutdown_workers(wait_ms: int = 1500) -> None:
    """Останавливает все живые воркеры перед выходом из приложения."""
    for worker in list(_ACTIVE_WORKERS):
        worker.stop()
    for worker in list(_ACTIVE_WORKERS):
        if not worker.wait(wait_ms):
            worker.terminate()  # крайняя мера при выходе: поток завис на I/O
            worker.wait(500)


class AIWorker(QThread):
    chunk = Signal(str)          # очередная дельта текста (только у стримящих провайдеров)
    finished_ok = Signal(str)    # полный итоговый текст
    failed = Signal(str)         # человекочитаемая ошибка

    def __init__(self, provider: Provider, prompt: str, timeout: int,
                 max_tokens: int = 64000):
        super().__init__()  # без Qt-родителя, см. _ACTIVE_WORKERS
        self.provider = provider
        self.prompt = prompt
        self.timeout = timeout
        self.max_tokens = max_tokens
        self._cancelled = False
        self._process: subprocess.Popen | None = None
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
        except Exception as exc:  # noqa: BLE001 — поток не должен падать молча
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

    # -- Anthropic API (официальный SDK, стриминг) ----------------------------

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
            self.failed.emit(tr("Anthropic API error ({code}): {message}")
                             .format(code=exc.status_code, message=exc.message))
        except anthropic.APIConnectionError:
            self.failed.emit(tr("Cannot connect to api.anthropic.com — check your network."))

    # -- OpenAI-совместимые API (OpenAI, Ollama, LM Studio, OpenRouter, ...) --

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
            hint = tr("Is Ollama running? (`ollama serve`)") if "11434" in base_url else ""
            self.failed.emit(
                tr("Cannot connect to {url}: {reason}").format(url=base_url, reason=exc.reason)
                + (f"\n{hint}" if hint else "")
            )
        except TimeoutError:
            self.failed.emit(tr("The provider did not respond within {timeout} s.")
                             .format(timeout=self.timeout))
