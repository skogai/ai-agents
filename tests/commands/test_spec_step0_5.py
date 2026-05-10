"""Tests for Step 0.5 Memory-First Gate in /spec command.

Refs #1951, REQ-008, DESIGN-008, TASK-008, plan req-008-step-0-5-memory-first-gate.

Verifies the static structure of Step 0.5 instructions in
`.claude/commands/spec.md` against the 12 acceptance criteria. Parser
logic lives in `tests/commands/step0_5_parser.py`; this file holds only
test cases.

Six dynamic LLM-dependent cases (D6, D7, D9, D12, D13, D14 from
TASK-008-5) are documented manual checks; eight other dynamic cases
(D1, D2, D3, D4, D5, D8, D10, D11) are promoted to pytest in M5.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.commands.step0_5_parser import (
    GUARD_STRING,
    compute_provisional_tier,
    extract_step0_5_block,
    extract_step0_5_subsection,
    extract_step9_block,
    has_guard_string,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SPEC_MD = PROJECT_ROOT / ".claude" / "commands" / "spec.md"

CANONICAL_DEFERRAL_TEXT = (
    "Revise Step 0 Q4 to name blast-radius entities or add explicit "
    "out-of-scope entries; then re-run Step 0.5."
)


@pytest.fixture(scope="module")
def spec_text() -> str:
    return SPEC_MD.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def step0_5_block(spec_text: str) -> str:
    return extract_step0_5_block(spec_text)


@pytest.fixture(scope="module")
def step9_block(spec_text: str) -> str:
    return extract_step9_block(spec_text)


# ---------------------------------------------------------------------------
# S1: heading order (AC-01)
# ---------------------------------------------------------------------------


def test_s1_heading_order_step_0_then_step_0_5_then_step_1(spec_text: str):
    step0 = spec_text.find("### Step 0: First Principles Gate")
    step0_5 = spec_text.find(
        "### Step 0.5: Memory-First Gate (blocking, runs after Step 0)"
    )
    step1 = re.search(r"^1\. Clarify the problem\.", spec_text, re.MULTILINE)

    assert step0 != -1, "Step 0 heading missing"
    assert step0_5 != -1, "Step 0.5 heading missing"
    assert step1 is not None, "Step 1 numbered-list anchor missing"
    assert step0 < step0_5 < step1.start(), (
        f"Heading order wrong: Step 0={step0}, Step 0.5={step0_5}, "
        f"Step 1={step1.start()}"
    )


# ---------------------------------------------------------------------------
# S2: ProvisionalTier mapping tables (AC-02)
# ---------------------------------------------------------------------------


def test_s2_provisional_tier_subsection_present(step0_5_block: str):
    assert (
        "#### Step 0.5 ProvisionalTier (auto-classified, no user prompt)"
        in step0_5_block
    )


def test_s2_provisional_tier_hours_mapping_strict_less_than(spec_text: str):
    body = extract_step0_5_subsection(
        spec_text,
        "#### Step 0.5 ProvisionalTier (auto-classified, no user prompt)",
    )
    assert "2 to less than 8 hours" in body
    assert "8 to less than 40 hours" in body
    assert "40 to less than 160 hours" in body


def test_s2_provisional_tier_max_formula_present(step0_5_block: str):
    assert "max(hours_tier, entity_tier)" in step0_5_block


def test_s2_provisional_tier_default_when_no_hours(step0_5_block: str):
    assert "default `hours_tier = 2`" in step0_5_block


# ---------------------------------------------------------------------------
# S3: chestertons-fence invocation (AC-03)
# ---------------------------------------------------------------------------


def test_s3_chestertons_fence_invocation_with_target_and_change(
    step0_5_block: str,
):
    assert 'Skill(skill="chestertons-fence")' in step0_5_block
    assert "target" in step0_5_block
    assert "change" in step0_5_block
    assert "Q3" in step0_5_block and "Q4" in step0_5_block


# ---------------------------------------------------------------------------
# S4: memory queries (AC-04)
# ---------------------------------------------------------------------------


def test_s4_memory_minimum_three_distinct_queries(step0_5_block: str):
    assert "search_memory.py" in step0_5_block
    assert "3 distinct query variants" in step0_5_block


# ---------------------------------------------------------------------------
# S5: knowledge-graph depth table (AC-05)
# ---------------------------------------------------------------------------


def test_s5_depth_table_per_provisional_tier(step0_5_block: str):
    assert 'Skill(skill="exploring-knowledge-graph")' in step0_5_block
    assert "Phases 1-2 (shallow)" in step0_5_block
    assert "Phases 1-4 (medium)" in step0_5_block
    assert "Phases 1-5 (deep)" in step0_5_block


# ---------------------------------------------------------------------------
# S6: zero-result coverage note rule (AC-06)
# ---------------------------------------------------------------------------


def test_s6_zero_result_coverage_note_rule(step0_5_block: str):
    assert (
        "Memory search returns 0 results for a topic after at minimum 3 distinct queries"
        in step0_5_block
    ) or (
        "after at minimum 3 distinct queries" in step0_5_block
        and "Coverage notes" in step0_5_block
    )


# ---------------------------------------------------------------------------
# S7: degradation rules (AC-07)
# ---------------------------------------------------------------------------


def test_s7_three_degradation_rules_present(spec_text: str):
    body = extract_step0_5_subsection(
        spec_text,
        "#### Step 0.5 degradation rules",
    )
    assert "chestertons-fence" in body
    assert "Forgetful MCP unavailable" in body
    assert "exploring-knowledge-graph" in body


# ---------------------------------------------------------------------------
# S8: entity adjudication and threshold (AC-08)
# ---------------------------------------------------------------------------


def test_s8_adjudication_three_categories(spec_text: str):
    body = extract_step0_5_subsection(
        spec_text,
        "#### Step 0.5 entity adjudication",
    )
    assert "in-scope" in body
    assert "out-of-scope" in body
    assert "blast-radius" in body


def test_s8_auto_mode_case_insensitive_match_rule(spec_text: str):
    body = extract_step0_5_subsection(
        spec_text,
        "#### Step 0.5 entity adjudication",
    )
    assert "case-insensitive" in body
    assert "auto-mode" in body or "Auto" in body


def test_s8_blast_radius_thresholds_2_human_3_auto(spec_text: str):
    body = extract_step0_5_subsection(
        spec_text,
        "#### Step 0.5 entity adjudication",
    )
    assert "Human" in body and "2 or more" in body
    assert "Auto" in body and "3 or more" in body


# ---------------------------------------------------------------------------
# S9: halt block format (AC-09)
# ---------------------------------------------------------------------------


def test_s9_step0_5_halt_info_string_with_five_fields(step0_5_block: str):
    assert "step0_5-halt" in step0_5_block
    for field in ("trigger", "check", "evidence", "test_failed", "deferral"):
        assert f"{field}:" in step0_5_block, f"halt field missing: {field}"


def test_s9_h11_trigger_documented(step0_5_block: str):
    assert "H11" in step0_5_block


def test_s9_canonical_deferral_text_present(step0_5_block: str):
    assert CANONICAL_DEFERRAL_TEXT in step0_5_block


# ---------------------------------------------------------------------------
# S10: supplemental Phase 5 trigger formula (AC-10)
# ---------------------------------------------------------------------------


def test_s10_phases_needed_formula_present(spec_text: str):
    body = extract_step0_5_subsection(
        spec_text,
        "#### Step 0.5 supplemental Phase 5 hook (cross-step)",
    )
    assert "phases_needed" in body
    assert "actual_tier > provisional_tier" in body
    assert "phases_needed(actual_tier) > phases_run(provisional_tier)" in body


def test_s10_supplemental_subblock_heading_documented(step0_5_block: str):
    assert "### Supplemental (Phase" in step0_5_block


# ---------------------------------------------------------------------------
# S11: metrics tally (AC-11)
# ---------------------------------------------------------------------------


def test_s11_metrics_file_path_and_format(spec_text: str):
    body = extract_step0_5_subsection(
        spec_text,
        "#### Step 0.5 metrics tally",
    )
    assert ".agents/sessions/STEP-0.5-METRICS.md" in body
    assert "<ISO-8601 timestamp> | <pass|fail>" in body
    assert "100 entries" in body


# ---------------------------------------------------------------------------
# S12: check 9d in Step 9 (AC-12)
# ---------------------------------------------------------------------------


def test_s12_check_9d_in_step_9(step9_block: str):
    assert "Check 9d, Prior Art / Constraints elicitation" in step9_block


def test_s12_check_9d_pass_condition_lists_three_subsections(step9_block: str):
    assert "### Direct prior art from memory" in step9_block
    assert "### Connected context from exploring-knowledge-graph" in step9_block
    assert "### Coverage notes" in step9_block


def test_s12_check_9d_includes_guard_string_fail_clause(step9_block: str):
    assert GUARD_STRING in step9_block, (
        "9d FAIL clause must reference guard string for partial-M2 detection"
    )


def test_s12_guard_string_absent_from_step_0_5_body(step0_5_block: str):
    """After commit 2B lands, the guard must not appear in the Step 0.5 body.

    The string IS allowed in Step 9 9d's FAIL-clause documentation (it
    names the guard so the check can detect it at runtime). The check
    `has_guard_string(spec_text)` returns True in normal post-2B state
    because of that documentation; per-block scoping is the correct
    invariant here.
    """
    assert GUARD_STRING not in step0_5_block, (
        "guard string `<!-- step0.5:incomplete-without-2b -->` must not "
        "appear in Step 0.5 body after commit 2B. It belongs ONLY in "
        "Step 9 9d FAIL clause documentation."
    )


# ---------------------------------------------------------------------------
# Reference cases for compute_provisional_tier (AC-02)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "q4_text, entity_count, expected",
    [
        ("6 hours", 1, 2),
        ("3 days", 5, 3),
        ("vague description with no number", 10, 4),
        ("2 weeks", 2, 4),
    ],
)
def test_compute_provisional_tier_reference_cases(
    q4_text: str, entity_count: int, expected: int
):
    assert compute_provisional_tier(q4_text, entity_count) == expected


def test_compute_provisional_tier_seven_point_nine_hours_is_tier_2():
    """REQ-008 AC-02: <8h is Tier 2 (Tier 2 range is `2 to less than 8`)."""
    assert compute_provisional_tier("7.9 hours", 1) == 2


def test_compute_provisional_tier_eight_hours_is_tier_3():
    """REQ-008 AC-02 boundary: 8h is in Tier 3 (range `8 to less than 40`).

    The Tier 2 range is `2 to less than 8` (strict upper bound), so 8h
    falls in Tier 3, not Tier 2. The mapping table is the canonical
    source; any contradicting parenthetical in prose is a doc bug.
    """
    assert compute_provisional_tier("8 hours", 1) == 3


def test_compute_provisional_tier_no_hours_defaults_to_tier_2():
    assert compute_provisional_tier("", 1) == 2
