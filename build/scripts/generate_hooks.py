#!/usr/bin/env python3
"""Generate Copilot CLI hook config from ``.claude/settings.json`` (REQ-003-007).

Reads ``artifacts.hooks`` from a platform YAML, parses Claude's
``settings.json`` ``hooks`` object, copies each registered Python script
under ``.claude/hooks/`` into the Copilot output tree, and emits a
``hooks.json`` with the Copilot wire shape (``version: 1`` wrapper,
lowercase event names, no ``matcher`` field, ``cwd``-relative invocation).

This module ships the **core** in M5-T1. The matcher shim injector
(REQ-003-007 step 5) and idempotency (M5-T3) are added in subsequent
commits. Until then, scripts are copied verbatim and the Copilot config
omits matcher information; entries with a Claude-side ``matcher``
silently cover all tool calls.

EXIT CODES:
  0 - success
  1 - logic error (script not found, copy failure)
  2 - configuration error (missing stanza, malformed JSON, etc.)

Per ADR-035 Exit Code Standardization.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence

_SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPT_DIR))

from regen_guard import detect_reason as regen_detect_reason  # noqa: E402
from yaml_loader import (  # noqa: E402
    ConfigError,
    load_platform_config,
    validate_relative_path,
)

_DEFAULT_TIMEOUT_SEC = 30


class GenerateHooksError(Exception):
    """Domain error for hook generation."""


@dataclass
class HookAuditEntry:
    """One hook script's outcome — used by tests and audit."""

    event_source: str  # Claude-side event (e.g. PreToolUse)
    event_target: str  # Copilot-side event (e.g. preToolUse) or "" if dropped
    script: str  # relative script path, e.g. PreToolUse/foo.py
    action: str  # "emitted" | "dropped" | "sentinel-skipped"
    matcher: str | None = None
    reason: str = ""


@dataclass
class GenerateHooksResult:
    """Aggregate result for a generation run.

    ``written`` counts emitted entries (one per Claude hook entry that
    survives event mapping); ``dropped`` counts entries whose event was
    in ``eventDrop``; ``sentinel_skipped`` counts target scripts not
    overwritten because of NO-REGEN sentinels.
    """

    written: int = 0
    dropped: int = 0
    sentinel_skipped: int = 0
    entries: list[HookAuditEntry] = field(default_factory=list)


# --- Stanza parsing -------------------------------------------------------


def _read_stanza(config_path: Path) -> dict[str, Any]:
    cfg = load_platform_config(config_path)
    artifacts = cfg.get("artifacts")
    if not isinstance(artifacts, dict):
        raise GenerateHooksError(
            f"{config_path}: missing `artifacts` mapping"
        )
    stanza = artifacts.get("hooks")
    if not isinstance(stanza, dict):
        raise GenerateHooksError(
            f"{config_path}: missing `artifacts.hooks` stanza"
        )
    # Required fields.
    for key in (
        "settingsSource",
        "scriptSource",
        "outputConfig",
        "outputScripts",
        "eventRemap",
    ):
        if key not in stanza:
            raise GenerateHooksError(
                f"{config_path}: artifacts.hooks missing required key `{key}`"
            )
    if not isinstance(stanza["eventRemap"], dict):
        raise GenerateHooksError(
            f"{config_path}: artifacts.hooks.eventRemap must be a mapping"
        )
    drops = stanza.get("eventDrop") or []
    if not isinstance(drops, list):
        raise GenerateHooksError(
            f"{config_path}: artifacts.hooks.eventDrop must be a list"
        )
    return stanza


def _resolve_paths(repo_root: Path, stanza: dict[str, Any]) -> dict[str, Path]:
    """Validate and resolve every path field; reject traversal/absolute."""
    resolved: dict[str, Path] = {}
    for field_name in (
        "settingsSource",
        "scriptSource",
        "outputConfig",
        "outputScripts",
    ):
        value = stanza[field_name]
        errs = validate_relative_path(field_name, value)
        if errs:
            raise GenerateHooksError("; ".join(errs))
        resolved[field_name] = repo_root / value
    return resolved


# --- Settings.json reader -------------------------------------------------


def _load_claude_settings(path: Path) -> dict[str, Any]:
    """Parse ``.claude/settings.json`` and return its ``hooks`` map."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise GenerateHooksError(f"settingsSource missing: {path}") from exc
    except OSError as exc:
        raise GenerateHooksError(f"read error for '{path}': {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GenerateHooksError(
            f"{path}: malformed JSON: {exc}"
        ) from exc
    hooks = data.get("hooks") if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        raise GenerateHooksError(
            f"{path}: top-level `.hooks` must be an object"
        )
    return hooks


# --- Script discovery and copy -------------------------------------------


def _resolve_script_path(
    script_source: Path, command: str, claude_event: str
) -> Path | None:
    """Map a Claude command string to its source script path.

    The Claude command shape is::

        python3 -u .claude/hooks/<Event>/<script>.py
        python3 -u .claude/hooks/<script>.py        (flat layout)

    Returns ``None`` when the command does not point at a script under
    ``.claude/hooks/`` (e.g. shell snippets); the generator skips those
    with a NOTICE rather than failing the build.
    """
    if not command:
        return None
    # Find the path token: last token ending in .py.
    tokens = command.split()
    py_tokens = [t for t in tokens if t.endswith(".py")]
    if not py_tokens:
        return None
    rel = py_tokens[-1].strip('"').strip("'")
    # Expected prefix .claude/hooks/. Strip and re-anchor under
    # ``script_source`` so this works regardless of whether the
    # command was authored relative to repo root or with an absolute
    # ``CLAUDE_PLUGIN_ROOT`` substitution.
    needle = ".claude/hooks/"
    if needle in rel:
        rel = rel.split(needle, 1)[1]
    candidate = script_source / rel
    if candidate.is_file():
        return candidate
    # Flat layout fallback: caller wrote ``.claude/hooks/foo.py`` but the
    # script lives at ``.claude/hooks/<Event>/foo.py``.
    nested = script_source / claude_event / rel
    if nested.is_file():
        return nested
    return None


def _relative_script_target(
    target_root: Path, target_event: str, script_name: str
) -> Path:
    return target_root / target_event / script_name


def _copy_script(
    source: Path,
    target: Path,
    *,
    what_if: bool,
) -> tuple[bool, str]:
    """Copy a script to ``target``, honoring NO-REGEN sentinel.

    Returns ``(written, reason)``. ``written=False`` when a sentinel is
    detected on the existing target; reason captures which sentinel.
    """
    reason = regen_detect_reason(target)
    if reason is not None:
        return False, f"NO-REGEN: {reason}"
    if what_if:
        return True, ""
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    return True, ""


# --- Entry building -------------------------------------------------------


def _build_copilot_entry(
    target_event: str,
    script_name: str,
    *,
    timeout_sec: int = _DEFAULT_TIMEOUT_SEC,
) -> dict[str, Any]:
    """Emit a single Copilot hook entry.

    The ``bash`` and ``powershell`` keys both invoke ``python3``. RQ #4
    in REQ-003 flags a Windows PATH risk for ``python3``; using ``py -3``
    on Windows handles the case where only ``python.exe`` is on PATH.
    """
    rel = f"./hooks/{target_event}/{script_name}"
    return {
        "type": "command",
        "bash": f'python3 -u "{rel}"',
        "powershell": f'py -3 -u "{rel}"',
        "cwd": ".",
        "timeoutSec": timeout_sec,
    }


# --- Driver ---------------------------------------------------------------


def _process_event(
    claude_event: str,
    groups: list[Any],
    *,
    event_remap: dict[str, str],
    event_drop: set[str],
    script_source: Path,
    output_scripts: Path,
    what_if: bool,
    result: GenerateHooksResult,
) -> list[tuple[str, dict[str, Any]]]:
    """Process all entries for one Claude event.

    Returns ``[(target_event, entry_dict), ...]`` for each emitted hook.
    Side effects: copies scripts; appends to ``result.entries``.
    """
    if claude_event in event_drop:
        # Drop with WARN per script.
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []) or []:
                if not isinstance(hook, dict):
                    continue
                cmd = hook.get("command", "") or ""
                src = _resolve_script_path(script_source, cmd, claude_event)
                script_rel = (
                    str(src.relative_to(script_source))
                    if src is not None
                    else cmd or "<unknown>"
                )
                result.entries.append(
                    HookAuditEntry(
                        event_source=claude_event,
                        event_target="",
                        script=script_rel,
                        action="dropped",
                        matcher=group.get("matcher"),
                        reason=f"event '{claude_event}' in eventDrop",
                    )
                )
                result.dropped += 1
                print(
                    f"  WARN: dropping {claude_event}/{script_rel} "
                    f"(event not supported by Copilot CLI)",
                    file=sys.stderr,
                )
        return []

    target_event = event_remap.get(claude_event)
    if not target_event:
        # Unknown event (not in remap and not in drop). Skip with WARN
        # rather than crash; the operator can extend the remap config.
        for group in groups:
            if not isinstance(group, dict):
                continue
            for hook in group.get("hooks", []) or []:
                cmd = hook.get("command", "") if isinstance(hook, dict) else ""
                result.entries.append(
                    HookAuditEntry(
                        event_source=claude_event,
                        event_target="",
                        script=str(cmd) or "<unknown>",
                        action="dropped",
                        matcher=group.get("matcher"),
                        reason=f"event '{claude_event}' not in eventRemap",
                    )
                )
                result.dropped += 1
                print(
                    f"  WARN: skipping unknown Claude event '{claude_event}' "
                    f"(not in eventRemap)",
                    file=sys.stderr,
                )
        return []

    emitted: list[tuple[str, dict[str, Any]]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        matcher = group.get("matcher")
        for hook in group.get("hooks", []) or []:
            if not isinstance(hook, dict):
                continue
            cmd = hook.get("command", "") or ""
            timeout = int(hook.get("timeout", _DEFAULT_TIMEOUT_SEC) or _DEFAULT_TIMEOUT_SEC)
            src = _resolve_script_path(script_source, cmd, claude_event)
            if src is None:
                # Non-script command (shell snippet) — skip with NOTICE.
                result.entries.append(
                    HookAuditEntry(
                        event_source=claude_event,
                        event_target=target_event,
                        script=cmd or "<empty>",
                        action="dropped",
                        matcher=matcher,
                        reason="not a Python script under .claude/hooks/",
                    )
                )
                result.dropped += 1
                continue

            script_rel = src.relative_to(script_source)
            script_name = src.name
            target = _relative_script_target(
                output_scripts, target_event, script_name
            )
            written, reason = _copy_script(src, target, what_if=what_if)
            if not written:
                result.entries.append(
                    HookAuditEntry(
                        event_source=claude_event,
                        event_target=target_event,
                        script=str(script_rel),
                        action="sentinel-skipped",
                        matcher=matcher,
                        reason=reason,
                    )
                )
                result.sentinel_skipped += 1
                # Still emit the Copilot config entry — the customer-owned
                # script is the whole point of NO-REGEN.
                entry = _build_copilot_entry(target_event, script_name, timeout_sec=timeout)
                emitted.append((target_event, entry))
                continue

            entry = _build_copilot_entry(
                target_event, script_name, timeout_sec=timeout
            )
            emitted.append((target_event, entry))
            result.entries.append(
                HookAuditEntry(
                    event_source=claude_event,
                    event_target=target_event,
                    script=str(script_rel),
                    action="emitted",
                    matcher=matcher,
                )
            )
            result.written += 1
    return emitted


def generate_hooks(
    config_path: Path,
    repo_root: Path,
    *,
    what_if: bool = False,
) -> tuple[int, GenerateHooksResult]:
    """Generate Copilot CLI hooks per the artifacts.hooks stanza.

    Returns ``(exit_code, result)`` so callers can inspect the audit
    without re-parsing logs.
    """
    print()
    print("=== Hooks -> Copilot ===")
    print(f"Config: {config_path}")
    print(f"Repo root: {repo_root}")
    print(f"Mode: {'WhatIf' if what_if else 'Generate'}")
    print()

    result = GenerateHooksResult()

    try:
        stanza = _read_stanza(config_path)
    except (ConfigError, GenerateHooksError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2, result

    event_remap_raw = stanza["eventRemap"]
    event_remap: dict[str, str] = {
        str(k): str(v) for k, v in event_remap_raw.items()
    }
    event_drop: set[str] = {
        str(item) for item in (stanza.get("eventDrop") or [])
    }
    version_field = int(stanza.get("versionField", 1) or 1)

    try:
        paths = _resolve_paths(repo_root, stanza)
    except GenerateHooksError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2, result

    settings_source = paths["settingsSource"]
    script_source = paths["scriptSource"]
    output_config = paths["outputConfig"]
    output_scripts = paths["outputScripts"]

    if not settings_source.is_file():
        print(f"Error: settingsSource not found: {settings_source}", file=sys.stderr)
        return 1, result
    if not script_source.is_dir():
        print(f"Error: scriptSource not a directory: {script_source}", file=sys.stderr)
        return 1, result

    try:
        hooks_map = _load_claude_settings(settings_source)
    except GenerateHooksError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2, result

    start = time.monotonic()
    print(f"Found {len(hooks_map)} Claude event(s) in {settings_source}")

    # Stable iteration order: alphabetical by Claude event name. Output
    # ordering is independent of dict insertion order.
    out: dict[str, list[dict[str, Any]]] = {}
    for claude_event in sorted(hooks_map.keys()):
        groups = hooks_map.get(claude_event)
        if not isinstance(groups, list):
            print(
                f"  WARN: {claude_event} value is not a list; skipping",
                file=sys.stderr,
            )
            continue
        emitted = _process_event(
            claude_event,
            groups,
            event_remap=event_remap,
            event_drop=event_drop,
            script_source=script_source,
            output_scripts=output_scripts,
            what_if=what_if,
            result=result,
        )
        for target_event, entry in emitted:
            out.setdefault(target_event, []).append(entry)

    # Write hooks.json (overwrite). NO-REGEN on the config file itself
    # protects manual customer edits.
    config_reason = regen_detect_reason(output_config)
    if config_reason is not None:
        print(
            f"  NOTICE: skipped {output_config} (NO-REGEN: {config_reason})"
        )
    else:
        wrapped = {"version": version_field, "hooks": out}
        if not what_if:
            output_config.parent.mkdir(parents=True, exist_ok=True)
            output_config.write_text(
                json.dumps(wrapped, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            print(f"  Would write: {output_config}")

    duration = time.monotonic() - start

    print()
    print("=== Summary ===")
    print(f"Duration: {duration:.2f}s")
    print(f"Written: {result.written}")
    if result.dropped:
        print(f"Dropped: {result.dropped}")
    if result.sentinel_skipped:
        print(f"Skipped (NO-REGEN sentinel): {result.sentinel_skipped}")

    return 0, result


# --- CLI ------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Platform YAML config (defaults to templates/platforms/copilot-cli.yaml).",
    )
    p.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="Repository root (defaults to script's grandparent).",
    )
    p.add_argument("--what-if", action="store_true", help="Dry-run mode.")
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = args.repo_root or _SCRIPT_DIR.parent.parent
    config_path = args.config or (
        repo_root / "templates" / "platforms" / "copilot-cli.yaml"
    )
    if not config_path.is_file():
        print(f"Error: config not found: {config_path}", file=sys.stderr)
        return 2
    rc, _result = generate_hooks(config_path, repo_root, what_if=args.what_if)
    return rc


if __name__ == "__main__":
    sys.exit(main())
