# <PROJECT_NAME> — Project Instructions

## Role
<项目一句话定位，如：一个 AI 网关服务>

## Build & Run

```bash
# 启动方式
docker compose up -d --build

# 或直接启动
uvicorn apps.main:app --host 127.0.0.1 --port 8000
```

## Test

```bash
# 单元测试
python -m pytest tests/ -q

# 带覆盖率
python -m pytest tests/ --cov=<module> --cov-report=term-missing -q
```

## Code Standards

- **Language**: Python 3.11+, `from __future__ import annotations` in all new files
- **Style**: ruff (line-length=100, select E/F/I/UP). Run `ruff check . && ruff format --check .`
- **Type Checking**: mypy on core modules. Run `mypy <module>/`
- **Architecture Decisions**: `docs/adr/` — mandatory for non-trivial choices
- **Process**: Issue → feature branch → PR → merge. No direct pushes to `main`.

## Environment

| Variable | Purpose |
|----------|---------|
| `(请补充)` | |
