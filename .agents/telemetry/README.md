# Push Guard Telemetry

EVENT lines from the push guards (M2/M3/M4/M5 and friends) land here. The
canonical schema is one JSON object per line; one JSONL file per guard per
week. The aggregator (`build/scripts/aggregate_guard_intercepts.py`) and
classifier (`build/scripts/classify_guard_maturity.py`) read these.

## Event contract

Every push guard built on `push_guard_base.py` emits structured lines on
stderr:

```text
EVENT={"guard": "<name>", "code": "E_<NAME>", "outcome": "block"|"fail_open", ...}
```

The `outcome` field distinguishes a real intercept (`block`) from a
framework-permitted bypass (`fail_open`). Each event also records
`violations`, `matched_files`, and `changed_files` for blocks, and
`reason` plus `detail` for fail-opens. See `.claude/hooks/PreToolUse/push_guard_base.py`
for the source of truth.

## Persistence

The production wiring is **TBD**. Today the aggregator can read either:

1. A directory of `*.jsonl` files in this folder. Each line must contain
   the JSON object emitted after the `EVENT=` prefix (one event per
   line, no other content).
2. STDIN piped from a one-shot capture script.

A follow-up will land the production capture pipeline (likely a
post-push hook that tees the EVENT lines into the canonical file). Until
then this directory exists so the aggregator has a stable read target
and can be exercised by tests using fixtures.

## Naming convention

`push-guard-events-YYYY-WW.jsonl` (ISO week). One file rolls per week
and never gets edited in place.

## Privacy

These events contain guard names, error codes, file counts, and
fail-open reasons. No file paths, no commit SHAs, no PII. Safe to keep
in the repository if needed; safer to keep out of git and rebuild from
the event log.
