"""Spawn the Antigravity CLI (`agy`) and capture its plain-text or stream-json output.

agy print mode uses Go-style flags:
  agy -p "<prompt>" [--model <id>] [--continue | --new-project]
      [--dangerously-skip-permissions] [--sandbox]
      [--print-timeout <duration>] [--output-format stream-json]
"""
from __future__ import annotations

import asyncio
import gc
import json
import os
import subprocess
from dataclasses import dataclass
from typing import Any, Callable, Coroutine

# Safety cap on stdout capture — prevents unbounded memory growth from
# runaway agy output. 1MB is generous (typical replies are 1–5KB).
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
        return f"🤖 <b>Запуск субагента{role_str}:</b>{prompt_str}"
    return f"🛠 <b>Действие:</b> <code>{html.escape(tool_name)}</code>"


def _build_args(
    *,
    agy_path: str,
    prompt: str,
    has_session: bool,
    model: str,
    mode: str,
    effort: str = "",
    print_timeout: str,
    chat_dir: str = "",
    conversation_id: str = "",
    stream_json: bool = True,
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
            "-p", prompt,
        ]
    else:
        args = [agy_path, "-p", prompt]

    if conversation_id:
        args.extend(["--conversation", conversation_id])
    elif has_session:
        args.append("--continue")
    else:
        args.append("--new-project")
    if model:
        args.extend(["--model", model])
    args.append("--dangerously-skip-permissions")
    if mode == "plan":
        args.append("--sandbox")
    args.extend(["--print-timeout", print_timeout])
    if stream_json:
        args.extend(["--output-format", "stream-json"])
    return args


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
    """Run agy in stream-json mode with real-time action & text streaming callbacks."""
    args = _build_args(
        agy_path=agy_path,
        prompt=prompt,
        has_session=has_session,
        model=model,
        mode=mode,
        effort=effort,
        print_timeout=print_timeout,
        chat_dir=chat_dir,
        conversation_id=conversation_id,
        stream_json=True,
    )
    _reap_zombies()
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=chat_dir or None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    stderr_chunks: list[str] = []

    async def _read_stderr() -> None:
        assert proc.stderr is not None
        while True:
            line = await proc.stderr.readline()
            if not line:
                break
            stderr_chunks.append(line.decode("utf-8", errors="replace"))

    accumulated_text = ""
    result_text = ""
    exit_code = 0

    async def _read_stdout() -> None:
        nonlocal accumulated_text, result_text, exit_code
        assert proc.stdout is not None
        while True:
            line_bytes = await proc.stdout.readline()
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

                # Realtime tool action notification
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

                # Realtime text streaming delta
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
                if res.get("status") != "SUCCESS":
                    exit_code = 1

    try:
        if timeout:
            await asyncio.wait_for(
                asyncio.gather(_read_stdout(), _read_stderr(), proc.wait()),
                timeout=timeout,
            )
        else:
            await asyncio.gather(_read_stdout(), _read_stderr(), proc.wait())
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return AgyResult(text="", exit_code=124, stderr=f"agy timed out after {timeout}s")
    except asyncio.CancelledError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise

    final_text = result_text or accumulated_text
    stderr_out = "".join(stderr_chunks).strip()
    return AgyResult(
        text=final_text,
        exit_code=proc.returncode if proc.returncode is not None else exit_code,
        stderr=stderr_out,
    )
