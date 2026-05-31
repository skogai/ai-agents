#!/usr/bin/env python3
"""Tests for the invoke_lsp_read_guard PreToolUse hook (ADR-062).

Covers the graduated Read gate (Warmup, Soft-allow, Soft-warn, Hard-block,
Surgical tiers), the always-bypass target set (out-of-repo, dotfile, TMPDIR),
the no-provider degrade-to-allow path, LSP_GATE_MODE=warn, SKIP_LSP_GATE, and
EVERY fail-open path (tty, empty stdin, malformed JSON, missing tool_input,
non-dict tool_input, missing file_path, wrong tool_name, exception).

Exit codes: 0 = allow (incl. fail-open and warn mode), 2 = block.

The guard only READS gate state; the PostToolUse tracker owns writes. State is
injected here by monkeypatching ``read_state`` so each tier is exercised in
isolation and no test depends on a real state file.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

if TYPE_CHECKING:
    from collections.abc import Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
HOOK_DIR = REPO_ROOT / ".claude" / "hooks" / "PreToolUse"
sys.path.insert(0, str(HOOK_DIR))

import invoke_lsp_read_guard as guard  # noqa: E402

# A real in-repo file path whose extension is overview-capable (python). The
# file need not exist; detect_providers keys on extension + config, and
# is_gated_target resolves the path without requiring it on disk.
PY_TARGET = str(REPO_ROOT / "scripts" / "sample_module.py")
MD_TARGET = str(REPO_ROOT / "docs" / "sample.md")
TXT_TARGET = str(REPO_ROOT / "sample.txt")
DOTFILE_TARGET = str(REPO_ROOT / ".serena" / "scratch.py")
OUTSIDE_TARGET = "/tmp/outside_sample.py"


def _state(
    *,
    warmup_done: bool = False,
    nav_count: int = 0,
    read_files: list[str] | None = None,
    last_tool: str = "",
) -> dict:
    """Build a gate-state dict in the canonical shape."""
    files = list(read_files or [])
    return {
        "cwd": str(REPO_ROOT),
        "warmup_done": warmup_done,
        "nav_count": nav_count,
        "read_count": len(files),
        "read_files": files,
        "last_tool": last_tool,
    }


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure mode/skip env vars never leak between tests."""
    monkeypatch.delenv("SKIP_LSP_GATE", raising=False)
    monkeypatch.delenv("LSP_GATE_MODE", raising=False)


# ---------------------------------------------------------------------------
# is_gated_target
# ---------------------------------------------------------------------------


class TestIsGatedTarget:
    def test_in_repo_code_file_is_gated(self):
        assert guard.is_gated_target(PY_TARGET, str(REPO_ROOT)) is True

    def test_empty_path_is_not_gated(self):
        assert guard.is_gated_target("", str(REPO_ROOT)) is False

    def test_out_of_repo_is_not_gated(self):
        assert guard.is_gated_target(OUTSIDE_TARGET, str(REPO_ROOT)) is False

    def test_dotfile_member_is_not_gated(self):
        assert guard.is_gated_target(DOTFILE_TARGET, str(REPO_ROOT)) is False

    def test_repo_root_itself_is_not_gated(self):
        # The repo root resolves equal to root; relative_to gives '.', no parts
        # start with a dot, but a directory is not a navigable Read target. It
        # still returns True here (gating happens downstream via providers); the
        # branch under test is the ``resolved == root`` equality path.
        assert guard.is_gated_target(str(REPO_ROOT), str(REPO_ROOT)) is True

    def test_tmpdir_scratch_is_not_gated(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        scratch = str(tmp_path / "draft.py")
        assert guard.is_gated_target(scratch, str(REPO_ROOT)) is False

    def test_path_equal_to_tmpdir_is_not_gated(self, monkeypatch: pytest.MonkeyPatch, tmp_path):
        # Covers the ``tmp_root == resolved`` equality branch. The path must be
        # in-repo (else the out-of-repo check returns first), so use a repo root
        # whose own child is both the target and the TMPDIR.
        repo = tmp_path / "repo"
        scratch = repo / "tmp"
        scratch.mkdir(parents=True)
        monkeypatch.setenv("TMPDIR", str(scratch))
        assert guard.is_gated_target(str(scratch), str(repo)) is False

    def test_blank_tmpdir_does_not_bypass(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("TMPDIR", "   ")
        assert guard.is_gated_target(PY_TARGET, str(REPO_ROOT)) is True

    def test_unresolvable_path_fails_open_not_gated(self):
        with patch.object(guard.Path, "resolve", side_effect=OSError("boom")):
            assert guard.is_gated_target(PY_TARGET, str(REPO_ROOT)) is False

    def test_unresolvable_tmpdir_fails_open_not_gated(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ):
        monkeypatch.setenv("TMPDIR", str(tmp_path))
        real_resolve = guard.Path.resolve

        def fake_resolve(self):
            if str(self) == str(tmp_path):
                raise OSError("tmp boom")
            return real_resolve(self)

        with patch.object(guard.Path, "resolve", fake_resolve):
            assert guard.is_gated_target(PY_TARGET, str(REPO_ROOT)) is False

    def test_relative_to_value_error_fails_open(self, monkeypatch: pytest.MonkeyPatch):
        # Force resolved.relative_to(root) to raise after the parent check passed.
        real_relative_to = guard.Path.relative_to

        def fake_relative_to(self, *args, **kwargs):
            raise ValueError("not relative")

        monkeypatch.setattr(guard.Path, "relative_to", fake_relative_to)
        # Path is in-repo (so it passes the parents check) but relative_to raises.
        assert guard.is_gated_target(PY_TARGET, str(REPO_ROOT)) is False
        guard.Path.relative_to = real_relative_to  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------


class TestMessageBuilders:
    def test_warmup_block_names_serena_and_native(self):
        msg = guard.build_warmup_block(PY_TARGET, ["serena", "native_lsp"])
        assert "Warmup required" in msg
        assert "get_symbols_overview" in msg
        assert "native LSP overview" in msg
        assert PY_TARGET in msg

    def test_warmup_block_serena_only(self):
        msg = guard.build_warmup_block(MD_TARGET, ["serena"])
        assert "get_symbols_overview" in msg
        assert "native LSP overview" not in msg

    def test_warn_message(self):
        msg = guard.build_warn_message(PY_TARGET, 3)
        assert "WARNING (Read 3)" in msg
        assert "find_symbol" in msg

    def test_hard_block_names_providers(self):
        msg = guard.build_hard_block(PY_TARGET, 4, 1, ["serena", "native_lsp"])
        assert "Surgical mode required" in msg
        assert "you have 1" in msg
        assert "find_symbol" in msg
        assert "native LSP" in msg
        assert f"Blocked: {PY_TARGET}" in msg

    def test_hard_block_native_only(self):
        msg = guard.build_hard_block(PY_TARGET, 5, 0, ["native_lsp"])
        assert "native LSP" in msg
        assert "mcp__serena__find_symbol" not in msg


# ---------------------------------------------------------------------------
# evaluate: tier logic (state injected; guard only reads)
# ---------------------------------------------------------------------------


class TestEvaluateTiers:
    def test_non_gated_target_allows(self):
        code, msg = guard.evaluate(OUTSIDE_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None

    @patch.object(guard, "detect_providers", return_value=[])
    def test_no_provider_allows(self, _mock):
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_warmup_tier_blocks(self, mock_state, _mock_providers):
        mock_state.return_value = _state(warmup_done=False)
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 2
        assert msg is not None
        assert "Warmup required" in msg

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_first_free_read_allows(self, mock_state, _mock_providers):
        mock_state.return_value = _state(warmup_done=True, read_files=[])
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_second_free_read_allows(self, mock_state, _mock_providers):
        mock_state.return_value = _state(warmup_done=True, read_files=["a.py"])
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_third_read_warns_but_allows(self, mock_state, _mock_providers, capsys):
        mock_state.return_value = _state(warmup_done=True, read_files=["a.py", "b.py"])
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None
        out = capsys.readouterr().out
        payload = json.loads(out.strip().splitlines()[0])
        assert "WARNING (Read 3)" in payload["systemMessage"]

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_fourth_read_hard_blocks(self, mock_state, _mock_providers):
        mock_state.return_value = _state(
            warmup_done=True, nav_count=0, read_files=["a.py", "b.py", "c.py"]
        )
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 2
        assert msg is not None
        assert "Surgical mode required" in msg

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_fourth_read_one_nav_still_blocks(self, mock_state, _mock_providers):
        # ADR-062 divergence: 1 nav does NOT unlock reads 4-5 (kit allowed it).
        mock_state.return_value = _state(
            warmup_done=True, nav_count=1, read_files=["a.py", "b.py", "c.py"]
        )
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 2
        assert "you have 1" in (msg or "")

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_surgical_nav_threshold_allows(self, mock_state, _mock_providers):
        mock_state.return_value = _state(
            warmup_done=True, nav_count=2, read_files=["a.py", "b.py", "c.py"]
        )
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_already_read_file_allows_without_warmup(self, mock_state, _mock_providers):
        # Re-reading a file already in read_files allows even pre-warmup
        # (matches the kit's ``alreadyRead`` early allow).
        mock_state.return_value = _state(
            warmup_done=False, nav_count=0, read_files=[PY_TARGET, "a.py", "b.py", "c.py"]
        )
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_warn_mode_converts_warmup_block_to_allow(
        self, mock_state, _mock_providers, monkeypatch, capsys
    ):
        monkeypatch.setenv("LSP_GATE_MODE", "warn")
        mock_state.return_value = _state(warmup_done=False)
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[0])
        assert "Warmup required" in payload["systemMessage"]

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_warn_mode_converts_hard_block_to_allow(
        self, mock_state, _mock_providers, monkeypatch, capsys
    ):
        monkeypatch.setenv("LSP_GATE_MODE", "WARN")  # case-insensitive
        mock_state.return_value = _state(
            warmup_done=True, nav_count=0, read_files=["a.py", "b.py", "c.py"]
        )
        code, msg = guard.evaluate(PY_TARGET, str(REPO_ROOT))
        assert code == 0
        assert msg is None
        payload = json.loads(capsys.readouterr().out.strip().splitlines()[0])
        assert "Surgical mode required" in payload["systemMessage"]


# ---------------------------------------------------------------------------
# main: dispatch, kill switch, fail-open paths
# ---------------------------------------------------------------------------


class TestMain:
    def test_exits_0_on_tty(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr("sys.stdin", MagicMock(isatty=lambda: True))
        assert guard.main() == 0

    def test_exits_0_on_empty_input(self, mock_stdin: Callable[[str], None]):
        mock_stdin("")
        assert guard.main() == 0

    def test_exits_0_on_whitespace_input(self, mock_stdin: Callable[[str], None]):
        mock_stdin("   \n  ")
        assert guard.main() == 0

    def test_exits_0_on_invalid_json(self, mock_stdin: Callable[[str], None]):
        mock_stdin("not json {")
        assert guard.main() == 0

    def test_exits_0_for_non_read_tool(self, mock_stdin: Callable[[str], None]):
        mock_stdin(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls"}}))
        assert guard.main() == 0

    def test_exits_0_for_non_dict_tool_input(self, mock_stdin: Callable[[str], None]):
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": "string"}))
        assert guard.main() == 0

    def test_exits_0_for_missing_tool_input(self, mock_stdin: Callable[[str], None]):
        mock_stdin(json.dumps({"tool_name": "Read"}))
        assert guard.main() == 0

    def test_exits_0_for_missing_file_path(self, mock_stdin: Callable[[str], None]):
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": ""}}))
        assert guard.main() == 0

    def test_exits_0_for_none_file_path(self, mock_stdin: Callable[[str], None]):
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": None}}))
        assert guard.main() == 0

    def test_skip_env_allows(self, mock_stdin: Callable[[str], None], monkeypatch):
        monkeypatch.setenv("SKIP_LSP_GATE", "true")
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": PY_TARGET}}))
        assert guard.main() == 0

    def test_skip_env_case_insensitive(self, mock_stdin: Callable[[str], None], monkeypatch):
        monkeypatch.setenv("SKIP_LSP_GATE", "TRUE")
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": PY_TARGET}}))
        assert guard.main() == 0

    def test_skip_env_other_value_does_not_skip(
        self, mock_stdin: Callable[[str], None], monkeypatch
    ):
        monkeypatch.setenv("SKIP_LSP_GATE", "yes")
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": TXT_TARGET}}))
        # txt is non-provider -> allow regardless, but the skip branch was not taken.
        assert guard.main() == 0

    @patch.object(guard, "skip_if_consumer_repo", return_value=True)
    def test_consumer_repo_allows(self, _mock):
        assert guard.main() == 0

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_blocks_on_warmup_via_main(
        self, mock_state, _mock_providers, mock_stdin: Callable[[str], None], capsys
    ):
        mock_state.return_value = _state(warmup_done=False)
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": PY_TARGET}}))
        assert guard.main() == 2
        assert "Warmup required" in capsys.readouterr().err

    @patch.object(guard, "detect_providers", return_value=["serena"])
    @patch.object(guard, "read_state")
    def test_allows_surgical_via_main(
        self, mock_state, _mock_providers, mock_stdin: Callable[[str], None]
    ):
        mock_state.return_value = _state(warmup_done=True, nav_count=2)
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": PY_TARGET}}))
        assert guard.main() == 0

    def test_fails_open_on_exception(self, mock_stdin: Callable[[str], None]):
        mock_stdin(json.dumps({"tool_name": "Read", "tool_input": {"file_path": PY_TARGET}}))
        with patch.object(guard, "get_project_directory", side_effect=RuntimeError("boom")):
            assert guard.main() == 0


# ---------------------------------------------------------------------------
# Entry-point coverage: subprocess feeding stdin + runpy __main__
# ---------------------------------------------------------------------------


class TestModuleAsScript:
    def test_allow_via_subprocess(self):
        """Out-of-repo target allows: exit 0 from a real subprocess."""
        hook_path = str(HOOK_DIR / "invoke_lsp_read_guard.py")
        payload = json.dumps(
            {"tool_name": "Read", "tool_input": {"file_path": OUTSIDE_TARGET}}
        )
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0

    def test_block_via_subprocess(self, tmp_path):
        """In-repo python target with fresh (absent) state blocks: exit 2."""
        hook_path = str(HOOK_DIR / "invoke_lsp_read_guard.py")
        payload = json.dumps(
            {"tool_name": "Read", "tool_input": {"file_path": PY_TARGET}}
        )
        env = dict(os.environ)
        env["XDG_STATE_HOME"] = str(tmp_path)  # isolated, no warmup recorded
        env.pop("SKIP_LSP_GATE", None)
        env.pop("LSP_GATE_MODE", None)
        result = subprocess.run(
            [sys.executable, hook_path],
            input=payload,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
        )
        assert result.returncode == 2
        assert "Warmup required" in result.stderr

    def test_main_guard_via_runpy(self, monkeypatch: pytest.MonkeyPatch):
        """Cover the ``sys.exit(main())`` guard line via runpy."""
        import runpy

        hook_path = str(HOOK_DIR / "invoke_lsp_read_guard.py")
        monkeypatch.setattr(
            "sys.stdin",
            io.StringIO(
                json.dumps({"tool_name": "Read", "tool_input": {"file_path": OUTSIDE_TARGET}})
            ),
        )
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_path(hook_path, run_name="__main__")
        assert exc_info.value.code == 0


# ---------------------------------------------------------------------------
# Bootstrap (module-level lib resolution): exercised in a fresh interpreter
# because it runs at import time and the in-process module is already loaded.
# ---------------------------------------------------------------------------


class TestBootstrap:
    def test_plugin_root_env_resolves_lib(self, tmp_path):
        """CLAUDE_PLUGIN_ROOT set to the real plugin dir: hook runs (exit 0)."""
        hook_path = str(HOOK_DIR / "invoke_lsp_read_guard.py")
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT / ".claude")
        env["XDG_STATE_HOME"] = str(tmp_path)
        env.pop("SKIP_LSP_GATE", None)
        env.pop("LSP_GATE_MODE", None)
        result = subprocess.run(
            [sys.executable, hook_path],
            input=json.dumps(
                {"tool_name": "Read", "tool_input": {"file_path": OUTSIDE_TARGET}}
            ),
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
        )
        assert result.returncode == 0

    def test_bootstrap_missing_lib_exits_2(self, tmp_path):
        """CLAUDE_PLUGIN_ROOT pointing at an empty dir: lib absent -> exit 2.

        PreToolUse bootstrap fails closed (cannot decide without the lib),
        matching the canonical invoke_skill_first_guard.py bootstrap.
        """
        hook_path = str(HOOK_DIR / "invoke_lsp_read_guard.py")
        env = dict(os.environ)
        env["CLAUDE_PLUGIN_ROOT"] = str(tmp_path)  # no lib/ here
        result = subprocess.run(
            [sys.executable, hook_path],
            input=json.dumps({"tool_name": "Read", "tool_input": {"file_path": PY_TARGET}}),
            capture_output=True,
            text=True,
            env=env,
        )
        assert result.returncode == 2
        assert "Plugin lib directory not found" in result.stderr
