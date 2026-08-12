# Session Handoff: ai-platform-lab

> Use this file when starting a new session to quickly understand project state.

## Project Context

AI Platform Lab — learning/interview AI platform reference implementation.
Phase A~R all delivered. See [CLAUDE.md](../CLAUDE.md) for full instructions.

## Current State

- **Active Phase:** None (post-delivery)
- **Active Feature:** None — all 38 features done
- **Last Session:** 2026-07-08 — Harness engineering completion (hooks, workflows, doc sync, scheduled tasks, feature_list.json, progress.md, session-handoff.md)
- **Branch:** main

## Quick Start

```bash
docker compose up -d --build           # Full stack
python -m pytest tests/ -q             # Unit tests
python -m pytest tests/ --cov=packages --cov-fail-under=60 -q  # With coverage
```

## Recent Changes

See `git log --oneline -10` or `CHANGELOG.md`.

## Key Files

| File | Purpose |
|------|---------|
| `CLAUDE.md` | Project instructions for Claude |
| `feature_list.json` | All 38 features with status + verification |
| `progress.md` | Current progress and next actions |
| `docs/00-PROJECT_STATUS.md` | Detailed project status |
| `docs/00-roadmap.md` | Roadmap + known limitations |
| `docs/00-closure-sop.md` | Capability closure checklist |

## Open Questions

- Static security audit (bandit/trivy) not yet added

## If Resuming Work

1. Read `progress.md` and `feature_list.json`
2. Check `git log --oneline -5` for latest commits
3. Run `python -m pytest tests/ -q` to verify baseline
4. Update `progress.md` before ending session
