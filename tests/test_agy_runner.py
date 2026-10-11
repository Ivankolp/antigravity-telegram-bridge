"""Tests for src.agy_runner — subprocess spawn and argv construction."""
from __future__ import annotations

import stat
import textwrap
from pathlib import Path

import pytest

from src.agy_runner import AgyResult, run_agy


@pytest.fixture
def fake_agy(tmp_path: Path) -> Path:
    """Create an executable shell stub that handles stream-json turns."""
    script = tmp_path / "agy"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '{"event":"init","conversation_id":"test-123","init":{}}'
            while IFS= read -r line; do
                echo '{"event":"step_update","step_update":{"step_type":"agent_response","text_delta":"fake reply"}}'
                echo '{"event":"result","result":{"status":"SUCCESS","response":"fake reply"}}'
                break
            done
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


async def test_run_agy_returns_stdout_text(fake_agy: Path, tmp_path: Path) -> None:
    result = await run_agy(
        prompt="hello",
        chat_dir=str(tmp_path),
        has_session=False,
        model="",
        mode="code",
        agy_path=str(fake_agy),
    )
    assert isinstance(result, AgyResult)
    assert result.exit_code == 0
    assert result.text.strip() == "fake reply"


async def test_run_agy_surfaces_nonzero_exit(tmp_path: Path) -> None:
    script = tmp_path / "agy-fail"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '{"event":"init","conversation_id":"test-fail","init":{}}'
            echo 'boom' >&2
            while IFS= read -r line; do
                echo '{"event":"result","result":{"status":"ERROR","error":"boom"}}'
                exit 1
            done
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    result = await run_agy(
        prompt="x",
        chat_dir=str(tmp_path),
        has_session=False,
        model="",
        mode="code",
        agy_path=str(script),
    )
    assert result.exit_code == 1
    assert "boom" in result.stderr
    assert result.text == ""


def test_build_worker_args_builds_argv() -> None:
    from src.agy_runner import _build_worker_args
    args = _build_worker_args(
        agy_path="/usr/bin/agy",
        has_session=True,
        model="gemini-2.5-pro",
        mode="plan",
        print_timeout="15m",
        chat_dir="/home/i/work",
    )
    assert "--input-format" in args and "stream-json" in args
    assert "--output-format" in args and "stream-json" in args
    assert "--continue" in args
    assert "--model" in args and "gemini-2.5-pro" in args
    assert "--dangerously-skip-permissions" in args
    assert "--sandbox" in args
    assert "--print-timeout" in args


def test_build_worker_args_uses_new_project_for_fresh_session() -> None:
    from src.agy_runner import _build_worker_args
    args = _build_worker_args(
        agy_path="/usr/bin/agy",
        has_session=False,
        model="",
        mode="code",
        chat_dir="/home/i/work",
    )
    assert "--new-project" in args
    assert "--continue" not in args


async def test_run_agy_returns_timeout_result(tmp_path: Path) -> None:
    script = tmp_path / "agy-hang"
    script.write_text(
        textwrap.dedent(
            """\
            #!/bin/sh
            echo '{"event":"init","conversation_id":"test-hang","init":{}}'
            while IFS= read -r line; do
                sleep 5
            done
            """
        )
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    result = await run_agy(
        prompt="x",
        chat_dir=str(tmp_path),
        has_session=False,
        model="",
        mode="code",
        agy_path=str(script),
        timeout=0.3,
    )
    assert result.exit_code == 124
    assert "timed out" in result.stderr
    assert result.text == ""


async def test_run_agy_no_timeout_by_default(fake_agy: Path, tmp_path: Path) -> None:
    result = await run_agy(
        prompt="x",
        chat_dir=str(tmp_path),
        has_session=False,
        model="",
        mode="code",
        agy_path=str(fake_agy),
    )
    assert result.exit_code == 0
    assert result.text.strip() == "fake reply"


def test_build_args_bubblewrap_sandbox(monkeypatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    from src.agy_runner import _build_args

    args = _build_args(
        agy_path="/usr/bin/agy",
        prompt="run-security-scan",
        has_session=False,
        model="gemini-2.5-pro",
        mode="plan",
        print_timeout="15m",
        chat_dir="/home/i/chat_workspace",
    )

    assert args[0] == "bwrap"
    assert "--unshare-net" in args
    assert "/home/i/chat_workspace" in args
    assert "/usr/bin/agy" in args
    assert "-p" in args and "run-security-scan" in args


def test_format_tool_action_ask_question() -> None:
    from src.agy_runner import _format_tool_action

    params = {
        "questions": [
            {
                "question": "Куда развернуть проект?",
                "options": ["Play2Go VPS", "Локальный сервер", "Docker swarm"],
            }
        ]
    }
    formatted = _format_tool_action("ask_question", params)
    assert "Вопрос от ассистента" in formatted
    assert "Куда развернуть проект?" in formatted
    assert "Play2Go VPS" in formatted
    assert "Локальный сервер" in formatted

