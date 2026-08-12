---
phase: W
reviewers: [claude, codex]
reviewed_at: 2026-08-12T13:00:00+08:00
plans_reviewed: [Commit bc19f51 — Phase W Self-Refine]
reviewer_status:
  claude: success
  codex: success
  gemini: skipped
  opencode: skipped
  qwen: skipped
  cursor: skipped
trimmed_reviewers: {}
---

# Cross-AI Code Review — Phase W: Self-Refine

## Consensus Top Concerns

### 1. `feedback_dimensions` not passed through gateway route (HIGH)

**Both reviewers flagged.** The route handler at `apps/gateway/agent/routes.py:534-543` does a manual field-by-field mapping from Pydantic `SelfRefineConfig` to dataclass `SelfRefineConfig`, but **omits `feedback_dimensions`**. Users sending custom dimensions via the API have them silently ignored; the dataclass always uses the 5 default dimensions.

**Fix:** Add `feedback_dimensions=tuple(src.feedback_dimensions) if src and src.feedback_dimensions else None` to the dataclass constructor.

### 2. Similarity convergence dead code — `packages.agent.tools.embedding` doesn't exist (HIGH)

**Both reviewers flagged.** The import path `packages.agent.tools.embedding` does not exist in the codebase. The `_check_similarity()` function always falls back to exact string matching (`current.strip() == previous.strip()`). This means:
- `similarity` strategy behaves like exact-match (convergence almost never detected)
- `hybrid` strategy always degrades to exact-match + LLM judge (extra cost with no benefit)

**Fix:** Either create `packages/agent/tools/embedding.py` with `compute_similarity`, or wire to the existing embedding service from Phase P.

### 3. `context` parameter dead code (MEDIUM)

`run_self_refine()` accepts a `context` parameter, and `generate()` uses it. But the gateway route never passes context — `AgentRunRequest` has no `context` field for self-refine. The code path is entirely dead at the API level.

**Fix:** Either add `context` to the API schema, or remove the parameter from `run_self_refine()`.

### 4. `cfg.enabled` never checked (MEDIUM)

`SelfRefineConfig.enabled` exists (default `True`) but is never checked in the orchestrator or route. Setting `enabled=False` has zero effect.

**Fix:** Check `cfg.enabled` at the start of `run_self_refine()`, bypass to single-shot when disabled.

### 5. `final_output` not truncated in API response (MEDIUM)

Research endpoint truncates to 500 chars (`result.report[:500]`). Self-Refine returns `result.final_output` verbatim. Inconsistent.

**Fix:** Truncate `final_message` to match Research endpoint pattern.

---

## Full Findings

| ID | Severity | Reviewer | Description |
|----|----------|----------|-------------|
| F1 | HIGH | both | `feedback_dimensions` not forwarded from Pydantic to dataclass in gateway route |
| F2 | HIGH | both | `packages.agent.tools.embedding` doesn't exist — similarity convergence always falls back to exact match |
| F3 | MEDIUM | both | `context` parameter dead code, never passed from API |
| F4 | MEDIUM | both | `cfg.enabled` flag exists but is never checked |
| F5 | MEDIUM | both | `final_output` not truncated (Research endpoint truncates to 500 chars) |
| F6 | MEDIUM | codex | Empty feedback conflated with optimal; `convergence_reason` should distinguish |
| F7 | LOW | codex | `dir()` fallback in exception handler is unusual and unnecessary |
| F8 | LOW | both | `except (ImportError, Exception)` double-catches ImportError |
| F9 | LOW | codex | Eval silently excludes no-answer questions from accuracy denominator |
| F10 | LOW | claude | Free-text feedback path unreachable through default config |
| F11 | LOW | claude | No `min_length` validation on `feedback_dimensions` in Pydantic schema |
| F12 | LOW | claude | `prompt` parameter in `convergence_check` is unused |

## Risk Assessment

**Overall: LOW-MEDIUM.** No logic bugs in the main loop. The two HIGH findings (F1, F2) are configuration/plumbing issues, not correctness bugs. The implementation is well-structured and follows established patterns.

## Recommendations

1. Apply fixes for F1, F3, F4, F5 (quick route/plumbing fixes)
2. Address F2 by wiring to existing embedding service or documenting the limitation
3. F6-F12 can be deferred to next iteration
