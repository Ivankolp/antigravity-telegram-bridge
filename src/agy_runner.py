"""Resident Antigravity CLI (`agy`) process worker and runner with stream-json bidirectional NDJSON.

Supports keeping a persistent `agy` process alive in the background for instant turns,
continuous execution of background tasks and timers, and real-time streaming of
actions and tokens.
"""
from __future__ import annotations

import asyncio
import gc
import json
import logging
import os
import signal
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

LOG = logging.getLogger("antigravity_telegram_bridge")

# Safety cap on stdout capture — prevents unbounded memory growth
_STDOUT_CAP_BYTES = 524_288  # 512 KiB


def _reap_zombies() -> None:
    try:
        while True:
            pid, _status = os.waitpid(-1, os.WNOHANG)
            if pid == 0:
                break
    except (ChildProcessError, OSError):
        pass


@dataclass(frozen=True)
class AgyResult:
    text: str
    exit_code: int
    stderr: str


def _format_tool_action(tool_name: str, params: dict[str, Any]) -> str:
    """Format an agent tool call into a full, detailed Telegram message without truncation."""
    import html
    if tool_name == "view_file":
        path = str(params.get("AbsolutePath", ""))
        start = params.get("StartLine")
        end = params.get("EndLine")
        extra = f" <i>(строки {start}–{end})</i>" if start and end else ""
        return f"📖 <b>Читаю файл:</b>\n<code>{html.escape(path)}</code>{extra}"
    elif tool_name == "write_to_file":
        path = str(params.get("TargetFile", ""))
        return f"✍️ <b>Записываю в файл:</b>\n<code>{html.escape(path)}</code>"
    elif tool_name == "replace_file_content":
        path = str(params.get("TargetFile", ""))
        inst = str(params.get("Instruction", "")).strip()
        inst_block = f"\n💡 <i>{html.escape(inst)}</i>" if inst else ""
        return f"✏️ <b>Редактирую файл:</b>\n<code>{html.escape(path)}</code>{inst_block}"
    elif tool_name == "run_command":
        cmd = str(params.get("CommandLine", "")).strip()
        cwd = str(params.get("Cwd", "")).strip()
        cwd_str = f" <i>[в <code>{html.escape(cwd)}</code>]</i>" if cwd else ""
        return f"⚙️ <b>Выполняю команду{cwd_str}:</b>\n<pre><code class=\"language-bash\">{html.escape(cmd)}</code></pre>"
    elif tool_name == "list_dir":
        path = str(params.get("DirectoryPath", "")).strip()
        return f"📁 <b>Просматриваю директорию:</b>\n<code>{html.escape(path)}</code>"
    elif tool_name == "search_web":
        q = str(params.get("query", "")).strip()
        return f"🌐 <b>Поиск в интернете:</b>\n<i>{html.escape(q)}</i>"
    elif tool_name == "grep_search":
        q = str(params.get("Query", "")).strip()
        sp = str(params.get("SearchPath", "")).strip()
        sp_str = f" <i>(в <code>{html.escape(sp)}</code>)</i>" if sp else ""
        return f"🔍 <b>Поиск по паттерну{sp_str}:</b>\n<code>{html.escape(q)}</code>"
    elif tool_name == "read_url_content":
        url = str(params.get("Url", "")).strip()
        return f"🌐 <b>Загружаю веб-страницу:</b>\n<code>{html.escape(url)}</code>"
    elif tool_name == "generate_image":
        prompt = str(params.get("Prompt", "")).strip()
        return f"🎨 <b>Генерация изображения:</b>\n<i>{html.escape(prompt)}</i>"
    elif tool_name == "invoke_subagent":
        role = str(params.get("Role", "")).strip()
        prompt = str(params.get("Prompt", "")).strip()
        role_str = f" <b>[{html.escape(role)}]</b>" if role else ""
        prompt_str = f"\n<i>{html.escape(prompt)}</i>" if prompt else ""
        return f"🤖 <b>Запускаю субагента{role_str}:</b>{prompt_str}"
    elif tool_name == "ask_question":
        raw_q = params.get("questions", [])
        if isinstance(raw_q, str):
            try:
                raw_q = json.loads(raw_q)
            except Exception:
                raw_q = []
        lines = ["❓ <b>Вопрос от ассистента:</b>"]
        if isinstance(raw_q, list):
            for item in raw_q:
                if not isinstance(item, dict):
                    continue
                q_text = str(item.get("question", "")).strip()
                if q_text:
                    lines.append(f"\n<b>{html.escape(q_text)}</b>")
                opts = item.get("options", [])
                if isinstance(opts, list) and opts:
                    for idx, opt in enumerate(opts, 1):
                        lines.append(f"  {idx}️⃣ {html.escape(str(opt))}")
        lines.append("\n💡 <i>Ответь текстом в чат или напиши номер варианта:</i>")
        return "\n".join(lines)
    return f"🛠 <b>Действие:</b> <code>{html.escape(tool_name)}</code>"


def _clean_response_tags(text: str) -> str:
    """Clean internal protocol and model tags from final text."""
    import re
    text = re.sub(r"(?is)<\s*NOTIFICATION\b[^>]*>.*?</\s*NOTIFICATION\s*>", "", text)
    text = re.sub(r"(?is)<\s*SYSTEM_MESSAGE\b[^>]*>.*?</\s*SYSTEM_MESSAGE\s*>", "", text)
    text = re.sub(r"(?is)<\s*system-reminder\b[^>]*>.*?</\s*system-reminder\s*>", "", text)
    text = re.sub(r"(?is)<\s*context\b[^>]*>.*?</\s*context\s*>", "", text)
    text = re.sub(r"(?is)<\s*thought\b[^>]*>.*?</\s*thought\s*>", "", text)
    text = re.sub(r"(?is)<\s*FINAL_RESPONSE\b[^>]*>(.*?)</\s*FINAL_RESPONSE\s*>", r"\1", text)
    text = re.sub(r"(?is)</?FINAL_RESPONSE\b[^>]*>", "", text)
    return text.strip()


def _build_worker_args(
    *,
    agy_path: str,
    has_session: bool,
    model: str,
    mode: str,
    effort: str = "",
    print_timeout: str = "15m",
    chat_dir: str = "",
    conversation_id: str = "",
) -> list[str]:
    agy_abs = os.path.abspath(agy_path)
    agy_parent = os.path.dirname(agy_abs)

    if mode == "plan" and chat_dir and "PYTEST_CURRENT_TEST" not in os.environ:
        chat_path = os.path.abspath(chat_dir)
        args: list[str] = [
            "bwrap",
            "--ro-bind", "/usr", "/usr",
            "--ro-bind", "/lib", "/lib",
            "--ro-bind", "/lib64", "/lib64",
            "--ro-bind", "/bin", "/bin",
            "--ro-bind", "/sbin", "/sbin",
            "--ro-bind", "/etc/alternatives", "/etc/alternatives",
            "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
            "--ro-bind", "/etc/ssl", "/etc/ssl",
            "--proc", "/proc",
            "--dev", "/dev",
            "--tmpfs", "/tmp",
            "--bind", chat_path, chat_path,
            "--chdir", chat_path,
            "--ro-bind", agy_parent, agy_parent,
            "--unshare-net",
            agy_abs,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
        ]
    else:
        args = [
            agy_path,
            "--input-format", "stream-json",
            "--output-format", "stream-json",
        ]

    if conversation_id:
        args.extend(["--conversation", conversation_id])
    elif has_session:
        args.append("--continue")
    else:
        args.append("--new-project")

    if model:
        args.extend(["--model", model])
    if effort:
        args.extend(["--effort", effort])
    args.append("--dangerously-skip-permissions")
    if mode == "plan":
        args.append("--sandbox")
    if print_timeout:
        args.extend(["--print-timeout", print_timeout])
    return args


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Send SIGTERM then SIGKILL to the entire process group to ensure child processes die."""
    try:
        pgid = os.getpgid(proc.pid)
        os.killpg(pgid, signal.SIGTERM)
        for _ in range(5):
            if proc.returncode is not None:
                break
            await asyncio.sleep(0.1)
        if proc.returncode is None:
            os.killpg(pgid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        await proc.wait()
    except Exception:
        pass
    _reap_zombies()


class ResidentWorker:
    """Persistent resident agy process maintaining a long-running stream-json session."""

    def __init__(self, chat_dir: str) -> None:
        self.chat_dir = chat_dir
        self.proc: asyncio.subprocess.Process | None = None
        self.lock = asyncio.Lock()
        self.current_config_key: tuple[str, str, str, str, str, bool] | None = None
        self.conversation_id: str = ""
        self._stderr_lines: list[str] = []
        self._stderr_task: asyncio.Task | None = None

    async def _read_stderr(self) -> None:
        try:
            while self.proc and self.proc.stderr:
                line = await self.proc.stderr.readline()
                if not line:
                    break
                self._stderr_lines.append(line.decode("utf-8", errors="replace"))
                if len(self._stderr_lines) > 200:
                    self._stderr_lines.pop(0)
        except Exception:
            pass

    async def ensure_running(
        self,
        *,
        agy_path: str,
        has_session: bool,
        model: str,
        mode: str,
        effort: str,
        conversation_id: str,
        print_timeout: str,
    ) -> bool:
        config_key = (self.chat_dir, model, mode, effort, conversation_id, has_session)
        if self.proc is not None and self.proc.returncode is None:
            if self.current_config_key == config_key:
                return True
            LOG.info("Resident worker config changed for %s, restarting...", self.chat_dir)
            await self.stop()

        self._stderr_lines.clear()
        args = _build_worker_args(
            agy_path=agy_path,
            has_session=has_session,
            model=model,
            mode=mode,
            effort=effort,
            print_timeout=print_timeout,
            chat_dir=self.chat_dir,
            conversation_id=conversation_id,
        )
        _reap_zombies()
        try:
            LOG.info(
                "Starting resident agy worker for %s (model=%s, mode=%s, effort=%s)...",
                self.chat_dir, model or "default", mode or "default", effort or "default"
            )
            self.proc = await asyncio.create_subprocess_exec(
                *args,
                cwd=self.chat_dir or None,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )
            self._stderr_task = asyncio.create_task(self._read_stderr())

            assert self.proc.stdout is not None
            init_line_bytes = await asyncio.wait_for(self.proc.stdout.readline(), timeout=20.0)
            if not init_line_bytes:
                LOG.error("Resident worker EOF during init in %s", self.chat_dir)
                await self.stop()
                return False

            init_data = json.loads(init_line_bytes.decode("utf-8", errors="replace"))
            self.conversation_id = init_data.get("conversation_id", "") or conversation_id
            self.current_config_key = config_key
            LOG.info("Resident agy worker started successfully (PID=%d, conv=%s)", self.proc.pid, self.conversation_id)
            return True
        except Exception as e:
            LOG.error("Failed to start resident agy worker in %s: %s", self.chat_dir, e)
            await self.stop()
            return False

    async def execute_turn(
        self,
        prompt: str,
        *,
        agy_path: str,
        has_session: bool,
        model: str,
        mode: str,
        effort: str,
        conversation_id: str,
        print_timeout: str,
        timeout: float | None = None,
        on_action: Callable[[str], Coroutine[Any, Any, None]] | None = None,
        on_delta: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
    ) -> AgyResult:
        async with self.lock:
            ok = await self.ensure_running(
                agy_path=agy_path,
                has_session=has_session,
                model=model,
                mode=mode,
                effort=effort,
                conversation_id=conversation_id,
                print_timeout=print_timeout,
            )
            if not ok or self.proc is None or self.proc.stdin is None or self.proc.stdout is None:
                err_text = "".join(self._stderr_lines).strip() or "Failed to start resident agy worker"
                return AgyResult(text="", exit_code=1, stderr=err_text)

            payload = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
            try:
                self.proc.stdin.write(payload.encode("utf-8"))
                await self.proc.stdin.drain()
            except Exception as e:
                LOG.warning("Failed to write to resident worker stdin: %s", e)
                await self.stop()
                return AgyResult(text="", exit_code=1, stderr=f"Worker write error: {e}")

            accumulated_text = ""
            result_text = ""
            exit_code = 0
            err_msg = ""

            async def _read_turn() -> None:
                nonlocal accumulated_text, result_text, exit_code, err_msg
                assert self.proc and self.proc.stdout
                while True:
                    line_bytes = await self.proc.stdout.readline()
                    if not line_bytes:
                        break
                    line_str = line_bytes.decode("utf-8", errors="replace").strip()
                    if not line_str:
                        continue

                    try:
                        event = json.loads(line_str)
                    except Exception:
                        accumulated_text += line_str + "\n"
                        continue

                    ev_type = event.get("event")
                    if ev_type == "step_update":
                        su = event.get("step_update", {})
                        stype = su.get("step_type")
                        state = su.get("state")

                        if stype == "tool" and state == "ACTIVE" and on_action:
                            tool_name = su.get("tool_name", "")
                            tool_info = su.get("tool_info", {})
                            params = tool_info.get("parameters", {}) if isinstance(tool_info, dict) else {}
                            act_str = _format_tool_action(tool_name, params)
                            if act_str:
                                try:
                                    await on_action(act_str)
                                except Exception:
                                    pass

                        elif stype == "agent_response":
                            delta = su.get("text_delta", "")
                            if delta:
                                accumulated_text += delta
                                if on_delta:
                                    try:
                                        await on_delta(delta, accumulated_text)
                                    except Exception:
                                        pass

                    elif ev_type == "result":
                        res = event.get("result", {})
                        result_text = res.get("response", "")
                        err_msg = res.get("error", "")
                        if res.get("status") != "SUCCESS":
                            exit_code = 1
                        break

            try:
                if timeout:
                    await asyncio.wait_for(_read_turn(), timeout=timeout)
                else:
                    await _read_turn()
            except asyncio.TimeoutError:
                await self.stop()
                return AgyResult(text="", exit_code=124, stderr=f"Resident worker timed out after {timeout}s")
            except asyncio.CancelledError:
                await self.stop()
                raise

            if self.proc.returncode is not None and not result_text and not accumulated_text:
                exit_code = self.proc.returncode or 1

            final_text = _clean_response_tags(result_text or accumulated_text)
            stderr_out = "".join(self._stderr_lines).strip()
            if err_msg:
                stderr_out = f"{err_msg}\n{stderr_out}".strip()

            return AgyResult(text=final_text, exit_code=exit_code, stderr=stderr_out)

    async def stop(self) -> None:
        if self._stderr_task:
            self._stderr_task.cancel()
            self._stderr_task = None
        if self.proc is not None:
            proc = self.proc
            self.proc = None
            self.current_config_key = None
            try:
                if proc.stdin and not proc.stdin.is_closing():
                    proc.stdin.close()
            except Exception:
                pass
            await _terminate_process_tree(proc)


_RESIDENT_WORKERS: dict[str, ResidentWorker] = {}
_WORKERS_LOCK = asyncio.Lock()


async def get_resident_worker(chat_dir: str) -> ResidentWorker:
    async with _WORKERS_LOCK:
        key = os.path.abspath(chat_dir)
        worker = _RESIDENT_WORKERS.get(key)
        if worker is None:
            worker = ResidentWorker(chat_dir=key)
            _RESIDENT_WORKERS[key] = worker
        return worker


async def stop_resident_worker(chat_dir: str) -> None:
    async with _WORKERS_LOCK:
        key = os.path.abspath(chat_dir)
        worker = _RESIDENT_WORKERS.pop(key, None)
        if worker is not None:
            await worker.stop()


async def stop_all_resident_workers() -> None:
    async with _WORKERS_LOCK:
        for worker in list(_RESIDENT_WORKERS.values()):
            try:
                await worker.stop()
            except Exception:
                pass
        _RESIDENT_WORKERS.clear()


async def run_agy(
    prompt: str,
    *,
    chat_dir: str,
    has_session: bool,
    model: str,
    mode: str,
    effort: str = "",
    agy_path: str,
    conversation_id: str = "",
    timeout: float | None = None,
    print_timeout: str = "15m",
    on_action: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    on_delta: Callable[[str, str], Coroutine[Any, Any, None]] | None = None,
) -> AgyResult:
    """Run agy turn via ResidentWorker (persistent stream-json process)."""
    worker = await get_resident_worker(chat_dir)
    return await worker.execute_turn(
        prompt=prompt,
        agy_path=agy_path,
        has_session=has_session,
        model=model,
        mode=mode,
        effort=effort,
        conversation_id=conversation_id,
        print_timeout=print_timeout,
        timeout=timeout,
        on_action=on_action,
        on_delta=on_delta,
    )
