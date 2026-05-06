# Guard Maturity Runbook

How to read the guard maturity report and what to do with it.

The push guards built on `.claude/hooks/PreToolUse/push_guard_base.py`
(M2 markdown lint, M3 manifest count, M4 session log, M5 PR description,
and any future siblings) emit structured `EVENT={...}` lines on stderr
for every block and every fail-open. The Hook Maturity Model classifies
each guard into one of six tiers based on age, intercept count, and a
fitness signal derived from block rate.

This runbook tells you what to do with the report.

## Generating the report

```bash
# From the repo root.
python3 build/scripts/aggregate_guard_intercepts.py \
  --guard markdown-lint --guard manifest-count \
  --guard pr-description --guard session-log \
  | python3 build/scripts/classify_guard_maturity.py
```

Or via the skill wrapper that reads the same telemetry source and
prints a sorted human-readable table:

```bash
python3 .claude/skills/guard-maturity/scripts/run_report.py
```

The wrapper prints the table on stdout and the raw JSON on stderr, so
both can be captured.

## Reading the report

Each row is one guard. Columns:

| Column | Meaning |
|---|---|
| `tier` | Hook Maturity Model classification |
| `guard` | Guard name (matches `EVENT.guard`) |
| `intercepts` | Total events: blocks + fail-opens |
| `fitness` | `block_rate - 0.5`. Positive = catching real issues; negative = mostly fail-opens or noise |
| `age_days` | Days since the first observed event for this guard |

The classifier sorts by tier severity (Harmful first, Inert second,
then Budding, Growing, Mature, Proficient). Within a tier it sorts by
intercept count descending. Read top-down.

## What to do per tier

| Tier | Threshold | Action | Why |
|---|---|---|---|
| **Harmful** | any age, ≥1 intercept, fitness < -0.02 | **Remove now**. Open an issue, delete the guard, send a PR. | A guard that fails open more than it blocks normalizes bypass. Other guards lose credibility. |
| **Inert** | ≥30 days, 0 intercepts | **Prune candidate**. Confirm via grep that the guard's globs have not matched any pushed file in the window, then remove. | A guard that has not fired for a month is paying nothing for the cognitive load it imposes on contributors. |
| **Budding** | <14 days | **Hold**. Watch the next two weeks. | Too young to score. Most new guards land here. |
| **Growing** | 14-30 days, ≥1 intercept | **Hold**. Re-evaluate at 30 days. | Adolescent. The guard fires; we do not yet know whether the catches are real. |
| **Mature** | ≥30 days, ≥5 intercepts, fitness ≥ 0 | **Keep**. Periodic review only. | The guard is settled and net-positive. Leave it alone. |
| **Proficient** | ≥60 days, ≥10 intercepts, fitness ≥ +0.02 | **Promote**. Add to the canonical guard list in PR templates and onboarding docs. Cite as evidence when reviewing related guards. | The guard catches real issues regularly. Other contributors should know it exists. |

## When to prune vs promote

Prune (move to Inert -> remove) when:

- Age is ≥30 days **and** intercepts is 0 across the whole telemetry
  window. Confirm by inspecting the source telemetry; an empty file is
  not the same as zero events.
- The guard's globs have demonstrably not matched any pushed file in
  the last 30 days. Run a quick grep over the canonical event log
  before deleting; if globs match but no events exist, the guard's
  validator may be silently fail-opening, in which case the right move
  is **fix**, not **prune**.

Promote (move from Mature to Proficient) when:

- Age is ≥60 days **and** intercepts is ≥10 **and** fitness is
  ≥ +0.02. Promotion is a formal step: update the PR template and
  onboarding docs to reference the guard.

Remove (Harmful) immediately when:

- Fitness is < -0.02 with at least one intercept. The guard is teaching
  contributors to ignore push guards. Removing a Harmful guard is
  always cheaper than letting it normalize bypass.

## Fitness in detail

`fitness = block_rate - 0.5`

| block_rate | fitness | Reading |
|---|---|---|
| 1.0 | +0.5 | Always blocks. Possibly noisy; check matched_files counts. |
| 0.7 | +0.2 | Mostly blocks. Healthy. |
| 0.5 | 0.0 | Half blocks, half fail-opens. Neutral. |
| 0.3 | -0.2 | Mostly fail-opens. Investigate root cause. |
| 0.0 | -0.5 | Always fails open. Harmful by definition. |

The +/-0.02 threshold for Harmful and Proficient guards small samples
from being labeled either way on a single noisy event.

When the codebase gains true positive vs noisy positive labels per
event, swap the formula in `compute_fitness()` without changing the
threshold table.

## Cadence

- Weekly: glance at the report. Promote anything Proficient. Open an
  issue for anything Harmful.
- Every 30 days: full pass. Prune Inert guards. Re-evaluate Growing
  guards.
- Before adding a new guard: run the report. Confirm no existing guard
  already covers the case at a higher tier than the new one would
  arrive at.

## Why this exists

The retrospective on PR #1887
(`.agents/retrospective/2026-05-05-pr-1887-iteration-paradox.md`)
documents that 95+ crystallized hooks earlier accumulated without
measurement. Each hook adds cognitive load on contributors and execution
cost on every push. Without a fitness scorecard, hooks accumulate as
dead weight.

The Hook Maturity Model is the scorecard. This runbook is how you act
on it.
