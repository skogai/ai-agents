# Execution Plan: Em/en-dash Prohibition Enforcement

## Metadata

| Field | Value |
|-------|-------|
| **Status** | In Progress |
| **Created** | 2026-05-09 |
| **Owner** | implementer |
| **Complexity** | Low |

## Objectives

- [ ] M1: Add MUST NOT rule to `.claude/rules/universal.md` and regenerate `.github/instructions/universal.instructions.md` via `build_all.py` (REQ-006-AC8)
- [ ] M2: Create test fixtures (`dash_violations.md`, `no_dash_clean.md`, `instructions_tree/`, `node_modules/`) and test skeleton `tests/hooks/test_dash_guard.py` (REQ-006-AC9, AC10, AC11)
- [ ] M3a: Add dash-check bash section to `.githooks/pre-commit` using `IS_MERGE` and `STAGED_MD_FILES` (REQ-006-AC1, AC2, AC4, AC5, AC6)
- [ ] M3b: Create `.githooks/commit-msg` bash hook for commit message dash check (REQ-006-AC3)
- [ ] M3c: Populate test assertions in `tests/hooks/test_dash_guard.py` for M3 hooks
- [ ] M4: Add `validate_dash_prohibition()` to `scripts/validation/pre_pr.py` and call from check runner (REQ-006-AC7)
- [ ] M4t: Add pytest coverage for `validate_dash_prohibition()` in `tests/test_validation_pre_pr.py`

## Decision Log

| Date | Decision | Rationale | Alternatives Considered |
|------|----------|-----------|------------------------|
| 2026-05-09 | Hook placements in `.githooks/` not `.claude/hooks/` | `.githooks/` are standard git hooks; fire for all commits (human + agent). `.claude/hooks/` is the Claude Code hook system, independent and does not support `commit-msg` event type | Claude Code PreToolUse hook on `Bash(git commit*)` — rejected: only intercepts `-m` inline messages, misses heredoc and `-F` forms |
| 2026-05-09 | Bash grep in pre-commit, not Python module | ADR-042 exception for `.githooks/` scripts; 15-line bash avoids subprocess latency on every commit; existing hook already uses bash `record_fail`/`record_pass` pattern | Python module at `scripts/validation/check_dashes.py` called from bash — would work but adds subprocess call on every commit |
| 2026-05-09 | Detect with `IS_MERGE`/`STAGED_MD_FILES` (canonical names) | Canonical variables per `.githooks/pre-commit:136` and `:186`; `MD_FILES` bash array is scoped only inside the markdown-lint block (line 254-258) | `IS_MERGE_COMMIT` (spec-generator initial name) — rejected: does not exist in the hook |
| 2026-05-09 | `validate_dash_prohibition()` returns `bool` not exit code | Matches existing `validate_*` pattern at `scripts/validation/pre_pr.py:177`; runner translates `False` to exit 1 | Returning exit code directly — inconsistent with all other validators |
| 2026-05-09 | Ref detection uses `push_guard_base.py` fallback chain | Canonical: `@{u}` then `refs/remotes/origin/HEAD` then `origin/main` (push_guard_base.py:328-368); hardcoding `origin/main` breaks forks | Hardcoded `origin/main` — rejected: breaks repos where remote is not `origin` or base branch is not `main` |
| 2026-05-09 | Spec files use `—`/`–` escape sequences | Specs discussing the dash prohibition must not themselves contain the prohibited characters (feedback_rule_under_construction memory); Python escape sequences produce the bytes at runtime without embedding them in the source file | Literal characters — rejected: spec files themselves trigger the prohibition check |

## Progress Log

| Date | Update | Agent |
|------|--------|-------|
| 2026-05-09 | Created plan. /spec lifecycle complete. PR #1928 open with REQ/DESIGN/TASK/INTERVIEW spec artifacts. Branch `feat/issue-1923-spec-em-en-dash-enforcement` pushed. | implementer |

## Blockers

- None. Spec PR #1928 under review. Implementation can begin once reviewer approves or in parallel on a new branch.

## Related

- Issue: #1923
- Spec PR: #1928 (`feat/issue-1923-spec-em-en-dash-enforcement`)
- REQ: `.agents/specs/requirements/REQ-006-em-en-dash-enforcement.md`
- DESIGN: `.agents/specs/design/DESIGN-006-em-en-dash-enforcement.md`
- TASK: `.agents/specs/tasks/TASK-006-em-en-dash-enforcement.md`
- INTERVIEW: `.agents/specs/interviews/INTERVIEW-1923-em-en-dash-enforcement.md`
