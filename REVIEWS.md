---
phase: W
reviewers: [codex]
reviewed_at: 2026-08-12T12:00:00+08:00
plans_reviewed: [.planning/PLAN.md]
reviewer_status:
  codex: success
  claude: skipped (self — running inside Claude Code)
  gemini: skipped (known auth failure from prior run)
  opencode: failed (empty output)
trimmed_reviewers: {}
---

# Cross-AI Plan Review — Phase W: Self-Refine

## Executive Note

This review covers the **Phase W Self-Refine implementation plan** (`.planning/PLAN.md`),
which addresses GitHub Issue #204 (updated: Phase V → W). The plan was independently
reviewed by **Codex CLI**, which produced a thorough analysis.

---

## Codex Review

### Summary

The Phase W plan is thorough and well-researched, mapping the Self-Refine algorithm to
the project's established patterns from ToT/Debate/Research. The implementation order is
logical (models -> engine -> API -> tests -> docs -> metadata). The plan correctly
identifies key pitfalls (self-blindness, runaway LLM calls, false convergence) and
includes sensible mitigations. The main concerns are around a small deviation from the
project's module structure convention, a definitional ambiguity in the hybrid convergence
strategy, statistical fragility of the quality gate benchmark, and a few missing edge
cases in the test coverage.

### Strengths

1. **Thorough pattern analysis** — Every integration point mapped against the
   ToT/Debate/Research precedent. Significantly reduces integration risk.

2. **LLM call budget enforcement** — `max_total_llm_calls` with hard upper limit (30)
   is well-designed. Essential since Self-Refine costs up to 4 calls per iteration.

3. **Graceful error degradation** — Retry-1 + degrade-to-empty for feedback() is a
   good pragmatic choice. Empty feedback causes a no-op pass-through, avoiding loop crash.

4. **Three convergence strategies** — Offering `llm_judged`, `similarity`, and `hybrid`
   gives flexibility. Defaulting to `hybrid` balances LLM cost against false-convergence risk.

5. **Model separation** — Supporting separate `generator_model` and `feedback_model`
   mitigates the "self-blindness" problem where the same model critiques its own output.

6. **Fail-open Result pattern** — Every error path returns a populated `SelfRefineResult`
   with `error` field set, matching codebase convention. `best-so-far` on timeout is
   a good practical choice.

7. **W4 naming distinction** — Explicitly distinguishing from `self_evolve.py` (Phase R)
   prevents a confusing naming collision.

### Concerns

#### MEDIUM: `engine.py` diverges from established module structure

The plan proposes `packages/agent/self_refine/engine.py` as the core orchestrator.
None of the existing modes use this convention:
- ToT: `tree.py` + `generator.py` + `evaluator.py` + `searcher.py`
- Debate: `models.py` + everything in `__init__.py`
- Research: `models.py` + `decomposer.py` + `searcher.py` + `synthesizer.py`

**Suggestion**: Either merge into `__init__.py` (Debate pattern), or rename to
`orchestrator.py` / `loop.py` for semantic clarity.

#### MEDIUM: Hybrid convergence definition needs formalization

The plan describes hybrid as "similarity quick check first, llm_judged to confirm if
below threshold" — sequential AND logic. But "hybrid" could also mean logical OR.
These give different results:
- Scenario: LLM says "converged" but similarity = 0.4 (below 0.85 threshold)
  - OR -> converged (stops)
  - Sequential AND -> unconverged (continues)

The sequential-AND is more conservative. Formalize as:
```
if similarity >= threshold -> converged (stop, skip LLM judge)
else -> ask LLM judge; if LLM says no improvement -> converged; else -> continue
```

#### LOW: Quality gate benchmark (10 GSM8K) is statistically fragile

The gate checks `self-refine(3) accuracy >= single-shot + 5%` on 10 problems. With only
10 samples, margin of error is roughly +/-15% at 95% CI. A 5% threshold is within the
noise floor. The gate could easily flake.

**Suggestion**: Increase to 30+ problems, or relax to no-regression check.

#### LOW: Missing hybrid-optimization test

W5 covers the three strategies individually but no test verifies that when similarity
is above threshold, the LLM judge call is skipped (the cost-saving behavior).

#### LOW: Context growth not addressed in plan

Each refinement round sends the full prompt + current output. After 5 iterations,
per-round context grows linearly. Should be documented as a known limitation.

#### LOW: `router_registry.py` not mentioned in plan

If ToT/Debate/Research are registered there, Self-Refine must also be. The modification
list should confirm this (or state it's unchanged).

### Suggestions

1. **Formalize hybrid convergence** as sequential-AND with pseudocode in the plan.
2. **Rename `engine.py` to `orchestrator.py`** or merge into `__init__.py`.
3. **Increase quality gate to 30+ problems**, or relax to no-regression.
4. **Add hybrid-optimization test** verifying similarity shortcut skips LLM judge call.
5. **Document context growth limitation** with a follow-up marker.
6. **Confirm `router_registry.py`** needs no changes (or add to modification list).

### Risk Assessment

**Overall: LOW-MEDIUM**

The plan is mature and addresses the critical concerns from the earlier issue-level review.
Remaining issues are structural (module naming) and definitional (hybrid convergence),
not correctness. Integration risk is low due to thorough pattern analysis.

| Risk Area | Level | Rationale |
|-----------|-------|----------|
| Algorithm correctness | LOW | Well-defined strategies, clear loop design |
| Cost/performance | LOW | max_total_llm_calls guard, hybrid optimization |
| Error handling | LOW | Retry-1 + degrade pattern for failures |
| Pattern consistency | MEDIUM | engine.py deviates from established naming |
| Test coverage | LOW | 15 tests; minor gap in hybrid optimization |
| Integration completeness | LOW | Mapped against all existing integration points |

---

## Consensus Summary

### Agreed Strengths

- Thorough pattern analysis with explicit integration mapping
- LLM call budget enforcement
- Graceful error degradation for feedback()
- Three convergence strategies with hybrid default
- Model separation (generator/feedback)
- Fail-open result pattern
- Explicit naming distinction from self_evolve.py

### Agreed Concerns

1. **MEDIUM** — `engine.py` naming deviates from established sub-module conventions
2. **MEDIUM** — Hybrid convergence strategy needs formalization
3. **LOW** — Quality gate benchmark (10 problems) statistically fragile
4. **LOW** — Missing hybrid-optimization test
5. **LOW** — Context growth not documented in plan
6. **LOW** — `router_registry.py` not in modification list

### Recommendations for Action

1. Rename `engine.py` to `orchestrator.py` or merge into `__init__.py`
2. Formalize hybrid convergence as sequential-AND with pseudocode
3. Increase quality gate to 30+ problems or relax to no-regression
4. Add hybrid-optimization test to W5 table
5. Document context growth as known limitation
6. Confirm router_registry.py status