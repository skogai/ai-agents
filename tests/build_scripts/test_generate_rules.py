"""Tests for build/scripts/generate_rules.py (REQ-003-006, M4-T2).

Coverage matrix:
- positive: scoped rules emit; paths -> applyTo; alwaysApply/priority dropped
- negative severity branches: high/medium/low without scope
- governance keyword scan (unset severity + body keyword)
- NO-REGEN sentinel honored
- configuration errors (missing stanza, traversal, etc.)
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "build" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "build"))

import generate_rules  # noqa: E402


# Helpers --------------------------------------------------------------------


def _write_rule(
    rules_dir: Path,
    name: str,
    *,
    frontmatter: str | None = None,
    body: str = "Rule body.\n",
) -> Path:
    rules_dir.mkdir(parents=True, exist_ok=True)
    path = rules_dir / f"{name}.md"
    if frontmatter is not None:
        content = f"---\n{frontmatter}---\n{body}"
    else:
        content = body
    path.write_text(content, encoding="utf-8")
    return path


def _write_config(tmp_path: Path) -> Path:
    cfg = tmp_path / "platform.yaml"
    cfg.write_text(
        """\
schemaVersion: "1.0"
provider: "test"
artifacts:
  rules:
    sourceDir: "rules_src"
    outputDir: "instr_out"
    sourceSuffix: ".md"
    outputSuffix: ".instructions.md"
    frontmatterRemap:
      paths: applyTo
    frontmatterDrop:
      - alwaysApply
      - priority
    skipIfNoPathScope: true
"""
    )
    return cfg


def _read_output(tmp_path: Path, name: str) -> str:
    return (tmp_path / "instr_out" / f"{name}.instructions.md").read_text(encoding="utf-8")


# Positive: scoped rules emit ------------------------------------------------


def test_scoped_rule_with_paths_remaps_to_applyTo(tmp_path: Path) -> None:
    """`paths:` must be renamed to `applyTo:` with value preserved."""
    _write_rule(
        tmp_path / "rules_src",
        "ci-scripts",
        frontmatter='paths: "scripts/**,build/**"\ndescription: "CI scripts"\n',
        body="# CI Scripts\n",
    )
    cfg = _write_config(tmp_path)

    rc, _ = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 0

    out = _read_output(tmp_path, "ci-scripts")
    assert "applyTo:" in out
    assert "scripts/**,build/**" in out
    assert "paths:" not in out.split("---")[1]  # only check frontmatter section
    assert "description:" in out


def test_scoped_rule_with_applyTo_preserved(tmp_path: Path) -> None:
    """`applyTo:` already in source must round-trip unchanged."""
    _write_rule(
        tmp_path / "rules_src",
        "testing",
        frontmatter='applyTo: "tests/**"\npriority: high\n',
        body="# Testing\n",
    )
    cfg = _write_config(tmp_path)
    assert generate_rules.generate_rules(cfg, tmp_path)[0] == 0

    out = _read_output(tmp_path, "testing")
    assert "applyTo: tests/**" in out
    assert "priority:" not in out.split("---")[1]


def test_alwaysApply_and_priority_dropped(tmp_path: Path) -> None:
    _write_rule(
        tmp_path / "rules_src",
        "scoped",
        frontmatter=(
            'paths: "src/**"\nalwaysApply: true\npriority: high\n'
            'description: "scoped rule"\n'
        ),
        body="body\n",
    )
    cfg = _write_config(tmp_path)
    assert generate_rules.generate_rules(cfg, tmp_path)[0] == 0

    out = _read_output(tmp_path, "scoped")
    fm = out.split("---")[1]
    assert "alwaysApply" not in fm
    assert "priority" not in fm
    assert "description: scoped rule" in fm
    assert "applyTo: src/**" in fm


def test_globs_treated_as_path_scope(tmp_path: Path) -> None:
    """`globs:` is a recognized scope key; rule should emit without severity gate."""
    _write_rule(
        tmp_path / "rules_src",
        "globsy",
        frontmatter='globs: "**/*.py"\n',
        body="body\n",
    )
    cfg = _write_config(tmp_path)
    assert generate_rules.generate_rules(cfg, tmp_path)[0] == 0
    assert (tmp_path / "instr_out" / "globsy.instructions.md").is_file()


# Severity branches ----------------------------------------------------------


def test_unscoped_high_severity_returns_1(tmp_path: Path) -> None:
    _write_rule(
        tmp_path / "rules_src",
        "danger",
        frontmatter="severity: high\n",
        body="body\n",
    )
    cfg = _write_config(tmp_path)
    rc, result = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 1
    assert result.high_severity_errors
    assert "danger" in result.high_severity_errors[0]
    assert not (tmp_path / "instr_out").exists()


def test_unscoped_medium_severity_skips_with_warn(
    tmp_path: Path, capsys: object
) -> None:
    _write_rule(
        tmp_path / "rules_src",
        "med",
        frontmatter="severity: medium\n",
        body="body\n",
    )
    cfg = _write_config(tmp_path)
    rc, result = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 0
    assert result.skipped_warn == 1
    assert result.written == 0
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "WARN" in captured.err
    assert "med" in captured.err


def test_unscoped_low_severity_skips_silently(tmp_path: Path, capsys: object) -> None:
    _write_rule(
        tmp_path / "rules_src",
        "noisy",
        frontmatter="severity: low\n",
        body="body\n",
    )
    cfg = _write_config(tmp_path)
    rc, result = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 0
    assert result.skipped_silent == 1
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert "WARN" not in captured.err


def test_unset_severity_with_governance_keyword_treated_as_high(tmp_path: Path) -> None:
    """`secret`, `credential`, `license`, `GP-001..008` escalate to high."""
    _write_rule(
        tmp_path / "rules_src",
        "leaky",
        frontmatter="description: 'has keyword'\n",
        body="Do not commit secrets in code.\n",
    )
    cfg = _write_config(tmp_path)
    rc, result = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 1
    assert any("leaky" in m for m in result.high_severity_errors)


def test_unset_severity_with_GP_keyword_treated_as_high(tmp_path: Path) -> None:
    """`GP-001` etc. are governance principle anchors and must escalate."""
    _write_rule(
        tmp_path / "rules_src",
        "gp",
        frontmatter=None,
        body="Implement GP-003 enforcement.\n",
    )
    cfg = _write_config(tmp_path)
    assert generate_rules.generate_rules(cfg, tmp_path)[0] == 1


def test_unset_severity_no_keyword_treated_as_medium(tmp_path: Path) -> None:
    """The default for unscoped + unset severity + no governance keyword is medium."""
    _write_rule(
        tmp_path / "rules_src",
        "philosophy",
        frontmatter=None,
        body="A neutral architectural musing.\n",
    )
    cfg = _write_config(tmp_path)
    rc, result = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 0
    assert result.skipped_warn == 1
    assert result.written == 0


# NO-REGEN sentinel ----------------------------------------------------------


def test_sentinel_html_comment_skips_overwrite(tmp_path: Path) -> None:
    _write_rule(
        tmp_path / "rules_src",
        "ci",
        frontmatter='paths: "scripts/**"\n',
        body="generated body\n",
    )
    out_dir = tmp_path / "instr_out"
    out_dir.mkdir(parents=True)
    target = out_dir / "ci.instructions.md"
    target.write_text("<!-- NO-REGEN -->\nhand-edited do not touch\n")

    cfg = _write_config(tmp_path)
    rc, result = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 0
    assert result.sentinel_skipped == 1
    assert "hand-edited" in target.read_text()


# Configuration errors -------------------------------------------------------


def test_missing_artifacts_rules_returns_2(tmp_path: Path) -> None:
    cfg = tmp_path / "p.yaml"
    cfg.write_text('schemaVersion: "1.0"\nprovider: "x"\n')
    rc, _ = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 2


def test_no_rules_found_returns_1(tmp_path: Path) -> None:
    (tmp_path / "rules_src").mkdir()
    cfg = _write_config(tmp_path)
    rc, _ = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 1


def test_absolute_source_dir_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "p.yaml"
    cfg.write_text(
        """\
schemaVersion: "1.0"
provider: "x"
artifacts:
  rules:
    sourceDir: "/etc/passwd"
    outputDir: "instr_out"
"""
    )
    rc, _ = generate_rules.generate_rules(cfg, tmp_path)
    assert rc == 2


# CLI entry point ------------------------------------------------------------


def test_main_invokes_generation(tmp_path: Path) -> None:
    _write_rule(
        tmp_path / "rules_src", "ok", frontmatter='paths: "**"\n', body="ok\n",
    )
    cfg = _write_config(tmp_path)
    rc = generate_rules.main([
        "--config", str(cfg), "--repo-root", str(tmp_path),
    ])
    assert rc == 0


def test_main_missing_config_returns_2(tmp_path: Path) -> None:
    rc = generate_rules.main([
        "--config", str(tmp_path / "nope.yaml"), "--repo-root", str(tmp_path),
    ])
    assert rc == 2
