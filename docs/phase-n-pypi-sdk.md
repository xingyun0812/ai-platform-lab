# Phase N — Python SDK 发布 PyPI

> **状态**：✅ 已完成 · tag `phase-n-pypi-sdk`
> **前置**：Phase L #63 `sdk_smoke.py` ✅ · SDK 源码 `sdk/python/`  
> **Tag**（完成后）：`phase-n-pypi-sdk`  
> **非目标**：TS SDK、多模态 Embedding、RBAC、SLO（留 Phase O+）

## 目标

把开发者体验从 `pip install -e sdk/python` 升级为 **`pip install ai-platform-lab`**（包名以 PyPI 可用性为准），并形成可重复的发布与 smoke 流程。

## 一句话面试讲法

「平台不只提供 HTTP API，还提供 **可版本化的 Python SDK**；CI 打 tag 自动发 PyPI，`sdk_smoke` 用安装后的包验 chat/rag/agent。」

## Issue 拆分

| # | 标题 | 依赖 | 工期 |
|---|------|------|------|
| N1 | 包元数据与 README | — | 1d |
| N2 | GitHub Actions 发布 PyPI | N1 | 1～2d |
| N3 | 发布后 `pip install` smoke | N2 | 1d |
| N4 | 文档与 roadmap 同步 | N3 | 0.5d |

## 技术要点

```mermaid
%%{init: {'theme': 'dark', 'themeVariables': { 'primaryColor': '#1e3a5f', 'primaryTextColor': '#e6edf3', 'lineColor': '#8b949e'}}}%%
flowchart LR
  A["sdk/python pyproject"] --> B["build wheel/sdist"]
  B --> C["GH Actions publish"]
  C --> D["pip install from PyPI"]
  D --> E["eval/sdk_smoke.py"]

  style C fill:#1a3a2a,stroke:#4ade80,color:#e6edf3
  style E fill:#1a3a2a,stroke:#4ade80,color:#e6edf3
```

### N1 — 包元数据

- 补 `sdk/python/README.md`（安装、Client 示例、环境变量）
- `[project]`：`authors`、`urls`（Homepage、Repository）
- 包名冲突时备选：`ai-platform-lab-sdk`（Issue 内记录决策）
- `__version__` 与 `pyproject.toml` 单源同步

### N2 — CI 发布

- `.github/workflows/publish-sdk.yml`：`workflow_dispatch` + tag `sdk-v*` 触发
- PyPI **Trusted Publishing**（OIDC，推荐）或仓库 Secret `PYPI_API_TOKEN`
- PR 变更 `sdk/python/**` 时仅 **build** 不上传

#### 维护者发布步骤

1. **PyPI 配置**（一次性）
   - [pypi.org](https://pypi.org) 注册项目 `ai-platform-lab`（若重名改用 `ai-platform-lab-sdk` 并改 `pyproject.toml`）
   - Trusted Publisher：Owner `xingyun0812`，Repo `ai-platform-lab`，Workflow `publish-sdk.yml`，Environment `pypi`
   - 或 Settings → Secrets → `PYPI_API_TOKEN`

2. **GitHub Environments**（可选审批）
   - `pypi` / `testpypi` environment，用于 `workflow_dispatch` 人工发版

3. **发版**
   ```bash
   #  bump sdk/python/ai_platform_lab/__init__.py __version__
   git tag sdk-v0.1.0 && git push origin sdk-v0.1.0
   # 或 Actions → Publish Python SDK → Run workflow
   ```

### N3 — Smoke

- `eval/sdk_pypi_smoke.sh`：venv 内 `pip install ai-platform-lab==<ver>` → `sdk_smoke.py`
- CI optional job（可用 TestPyPI）

### N4 — 文档

- `README.md` 安装段、`interview-narrative.md` SDK 层
- `PROJECT_STATUS.md` / `roadmap.md` 开发者体验 ✅ PyPI

## 验证

```bash
cd sdk/python && python -m build
pip install dist/*.whl
python -c "from ai_platform_lab import Client; print(Client)"
./eval/sdk_smoke.py --base-url http://127.0.0.1:8000
```

## 诚实边界（面试主动说）

- 发布的是 **HTTP 客户端 SDK**，不包含 Gateway 服务端
- 首版 **0.1.0**，SemVer；breaking 变更走 minor/major
- TestPyPI 可先验再发生产 PyPI
