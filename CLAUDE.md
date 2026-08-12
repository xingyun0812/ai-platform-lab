# ai-platform-lab — Project Instructions

## Role
AI Platform Lab — a learning / interview-oriented AI platform reference implementation.
Phase A–R delivered: gateway, RAG (incremental + multimodal), Agent (Planner/CoT/Multi-Agent/Harness),
eval pipeline, observability, hardening, Prompt/MCP/Embedding/HITL, security/compliance, SDK/PyPI,
Console V2, production Helm, architecture deepening.

## Build & Run

```bash
# Start full stack
docker compose up -d --build          # postgres + redis + gateway :8000 + worker + qdrant

# Or start gateway only (no deps)
uvicorn apps.gateway.main:app --host 127.0.0.1 --port 8000

# Health check after start
curl -sf http://127.0.0.1:8000/healthz
```

## Test

```bash
# Unit tests (no external deps)
python -m pytest tests/ -q

# Unit tests with coverage
python -m pytest tests/ --cov=packages --cov-report=term-missing -q

# Eval pipeline (no API key → skip live calls)
python eval/run.py run-eval --sample-limit 10

# Eval gate vs baseline
python eval/run.py gate --threshold 5

# Offline agent / multimodal / harness gates
python eval/agent_jd2_gate.py run
python eval/multimodal_embedding_gate.py run
python eval/harness_capability_gate.py run

# Smoke E2E (needs stack running)
python eval/acceptance_smoke.py
```

## Code Standards

- **Language**: Python 3.11, `from __future__ import annotations` in all new files
- **Style**: ruff (line-length=100, select E/F/I/UP). Run `ruff check . && ruff format --check .`
- **Architecture Decisions**: `docs/adr/ADDR-*.md` — mandatory for non-trivial choices
- **Packages → Platform facade**: `packages/` MUST NOT import `apps.gateway`; depend on `packages.platform` via `PlatformPort` protocol
- **Worker**: MUST NOT import `apps.gateway.rag`
- **Process**: Issue → feature branch → PR (with 3-line acceptance alignment) → merge. No direct pushes to `main`.

## Config & Deploy

| Purpose | Path |
|---------|------|
| Platform YAML configs | `config/*.yaml` |
| Docker Compose | `docker-compose.yml` |
| Dockerfile | `Dockerfile` |
| Helm chart | `deploy/helm/` |
| K8s manifests | `deploy/k8s/` |

## Key Architecture Files

- `docs/00-architecture.md` — system overview
- `docs/00-roadmap.md` — future plans & gaps
- `docs/00-PROJECT_STATUS.md` — current status
- `docs/adr/` — architecture decision records (3 so far)

## Environment

| Variable | Purpose |
|----------|---------|
| `LLM_API_KEY` | Provider API key |
| `LLM_BASE_URL` | Provider base URL |
| `DATABASE_URL` | Postgres connection string |
| `EVAL_API_KEY` | Eval pipeline API key (≈LLM_API_KEY) |

## Memory & Documentation

This project has a persistent memory system. Claude loads it automatically each session:

- **Memory index**: `/Users/zhangyue/.claude/projects/-Users-zhangyue-IdeaProjects-ai-platform-lab/memory/MEMORY.md`
  - `user-profile` — developer role, tech stack, language preference
  - `project-constraints` — collaboration workflow red lines
  - `project-commands` — quick command reference
  - `architecture-decisions` — ADR snapshot index
  - `known-issues` — gotchas, workarounds, honest gaps
  - `recurring-tasks` — SOP for releases, gate checks, phase closure

### Session State

| File | Purpose | When to update |
|------|---------|----------------|
| `feature_list.json` | All 38 features with status + verification evidence | When adding/removing features |
| `progress.md` | Current active feature, recent milestones, next action | Start and end of each session |
| `session-handoff.md` | Quick context restore for multi-session work | End of session if work is interrupted |

Key reference docs:
- `docs/00-closure-sop.md` — capability closure checklist
- `CHANGELOG.md` — release history
- `docs/00-roadmap.md` — future plans & gaps

## Harness Hooks & Workflows

| Purpose | Path |
|---------|------|
| Pre-commit hook | `.claude/hooks/pre-commit` — ruff + pre-commit 框架 |
| Pre-push hook | `.claude/hooks/pre-push` — 禁止直推 main |
| CI 监控工作流 | `.claude/workflows/ci-monitor.md` — 触发: `/workflow ci-monitor` |
| Eval 门禁工作流 | `.claude/workflows/eval-gate-runner.md` — 触发: `/workflow eval-gate-runner` |
| Phase 收尾工作流 | `.claude/workflows/phase-closure.md` — 触发: `/workflow phase-closure` |
| 文档同步检查 | `scripts/check_doc_sync.py` — 验证代码 API 与文档同步 (`--ci` 模式 50% 阈值) |
| Nightly eval | Scheduled task — 工作日 6:42 AM 自动运行 eval 门禁 |

## Ruff Config

See `pyproject.toml` `[tool.ruff]` — line-length=100, target-version=py311.
Rules: E (pycodestyle errors), F (pyflakes), I (import sorting), UP (pyupgrade).
