# PR #1897 — 7-Round /pr-review Trajectory

**Last Updated**: 2026-05-08
**Session Reference**: feat/evidence-standards-implementer-1890

## Constraints (HIGH confidence)

- **Single-source-of-truth violations cluster as N parallel bot threads.** When the same root cause fires across 8+ file locations on a single rescan, do NOT patch each file in isolation. The framing in the source artifact is wrong; retire it once. PR #1897 round 7: 8 of 17 threads were "model_tier=opus contradicts the cheaper-tier reviewer claim" across templates + 6 generated copies + spec-coverage gate. Rewriting the asymmetry framing in 3 templates + 6 manual syncs + 1 PR-description update closed the cluster. Two prior rounds (5, 6) tried to patch it locally, did not.
  - **How to apply**: When triaging a fresh wave of bot threads, group by root cause before by file. If one cluster has ≥4 threads with the same gist, treat it as a framing/spec problem first.
- **PR description and linked issue text are spec inputs to CI.** The "Validate Spec Coverage" gate reads PR body + Closes/Fixes #N text + implementation, then fails on contradiction. Code-only overrides (e.g. user changes implementer model_tier from sonnet to opus) need the description AND the linked issue updated, not just the code. Re-running the failing workflow does NOTHING if the spec it reads still claims the original target. (Already captured in global feedback memory `feedback_pr_description_is_spec_input.md`.)
- **Security findings are CI gates, not discussions.** Replying `/fp` to a CI-blocking semgrep finding only marks it triaged in the Semgrep platform; the next push runs a fresh scan that re-flags it. Fix in code (break the taint flow, eliminate the source, add an explicit sanitizer at the sink), not in triage. PR #1897 round 5/6: I `/fp`'d 4 semgrep CWE-78 findings; round 6 was a clean code fix that broke the env-var taint flow. (Already captured in `feedback_security_findings.md`.)
- **Generator-owned files are not hand-authored mirrors.** Before treating a file as a copy you sync manually, grep `build/scripts/build_all.py` and `templates/platforms/*.yaml` for the output path. PR #1897 round 7: I committed `.github/instructions/canonical-source-mirror.instructions.md` with hand-widened frontmatter; the next CI run failed staleness because the generator strips internal-only globs.

## Preferences (MED confidence)

- **Atomic commits ≤5 files split by concern.** AGENTS.md cap; round 7 split 22 files into 7 commits along template + copilot regen + vscode regen + claude-agents sync + claude-src sync + skill docs + validator+test boundaries. Each commit message led with the WHY (which bot thread cluster, which root cause), not WHAT.
- **Stage reply bodies in `.pytest_tmp/pr<num>/` before dispatch.** Path-traversal guard in `add_pr_review_thread_reply.py` rejects `/tmp/` paths (CWE-22 defense). `.pytest_tmp/` is gitignored and within repo root, so it's the right scratch location. Stage all bodies, then run a single bash loop calling the script per thread-id.
- **Run `build_all.py --check` locally as the last gate before pushing.** Catches generator staleness that CI rejects. Cheaper to fix locally than across two CI rounds.

## Edge Cases (MED confidence)

- **Linter that "corrects" generator output in working tree:** trust it. If `git status` shows a generated file modified after a regen and the diff matches the generator's output, the prior commit was wrong, not the linter. Round 7's `.github/instructions/canonical-source-mirror.instructions.md` drift was the linter restoring generator-correct content.
- **`gh run rerun --failed` does not retrigger workflows that depend on the PR description matching the code.** If the spec-coverage validator reads the PR body, a code-only fix won't change the rerun verdict. Update the description AND rerun.
- **Bot rescan compounds across vendors:** Copilot + CodeRabbit + semgrep + Cursor each file separate threads on every push, often on the same root cause from different angles. Round counts: 14 → 7 → 17 unresolved across rounds 4-6. (Already captured in `feedback_bot_reviewer_concurrency.md`.)
- **Same-skill same-PR re-invocation is a useful signal.** When `/pr-review` round N+1 surfaces the same finding from round N (different bot, same gist), the round-N fix did not address the root cause.

## Notes for Review (LOW confidence)

- Auto-mode `/loop` + `ScheduleWakeup` worked well for waiting on long-running CI checks (~90-120s). Cache stayed warm at 90-120s windows; 5min would have cost a cache miss.
- The session log's commit count grew faster than file changes because of the atomic-commit discipline. ≤5 files per commit means a 22-file change becomes 5-7 commits.
- Bot threads asking variations of the same question across 7 rounds suggested the underlying claim should change, not the surface text. The asymmetry framing rewrite (round 7) was overdue by 2 rounds.
