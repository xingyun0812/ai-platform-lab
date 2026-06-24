# Phase N Issue Backlog — Python SDK 发布 PyPI

> ✅ **Phase N 已完成**（tag `phase-n-pypi-sdk`）。验收以 [phase-n-pypi-sdk.md](./phase-n-pypi-sdk.md) 为准。

> 规划：[phase-n-pypi-sdk.md](./phase-n-pypi-sdk.md)  
> **Milestone**：Phase N — PyPI SDK  
> **Tag**：`phase-n-pypi-sdk`

| Backlog | GitHub Issue |
|---------|--------------|
| 规划文档 | [#76](https://github.com/xingyun0812/ai-platform-lab/issues/76) |
| N1 包元数据 | [#77](https://github.com/xingyun0812/ai-platform-lab/issues/77) | ✅ #82 |
| N2 CI 发布 | [#78](https://github.com/xingyun0812/ai-platform-lab/issues/78) | ✅ #83 |
| N3 pip smoke | [#79](https://github.com/xingyun0812/ai-platform-lab/issues/79) | ✅ #84 |
| N4 文档同步 | [#80](https://github.com/xingyun0812/ai-platform-lab/issues/80) | ✅ #85 |

---

## N1 — SDK 包元数据与 README

**目标**：`sdk/python` 满足 PyPI 上架最低要求。

**验收**（✅ 已完成）：
- [x] `sdk/python/README.md`（安装 + Quickstart）
- [x] `pyproject.toml`：`authors`、`urls`、`classifiers`
- [x] `__version__` 与 pyproject 一致
- [x] `python -m build` 本地可产出 wheel

**文件**：`sdk/python/README.md`、`sdk/python/pyproject.toml`、`sdk/python/ai_platform_lab/__init__.py`

**预估工期**：1d

---

## N2 — GitHub Actions 发布 PyPI

**目标**：可重复、安全的自动/半自动发布。

**验收**（✅ 已完成）：
- [x] `.github/workflows/publish-sdk.yml`
- [x] Trusted Publishing 或 `PYPI_API_TOKEN` 文档写入 `.env.example` 注释 / `docs/phase-n-pypi-sdk.md`
- [x] `workflow_dispatch` 可手动发版

**依赖**：N1

**预估工期**：1～2d

---

## N3 — 发布后 pip install smoke

**目标**：证明 PyPI 包能跑通 `sdk_smoke`。

**验收**（✅ 已完成）：
- [x] `eval/sdk_pypi_smoke.sh`（venv + pip install + smoke）
- [x] README 增加「从 PyPI 安装」段落

**依赖**：N2

**预估工期**：1d

---

## N4 — 文档与 roadmap 同步

**目标**：项目状态与面试叙事反映 PyPI 闭环。

**验收**（✅ 已完成）：
- [x] `docs/PROJECT_STATUS.md` 开发者体验 PyPI ✅
- [x] `docs/roadmap.md` 已知限制更新
- [x] `docs/interview-narrative.md` SDK 段 `pip install`
- [x] `CONTRIBUTING.md` Phase N 映射

**依赖**：N3

**预估工期**：0.5d
