# 能力闭环 SOP（Closure Standard Operating Procedure）

> **原则**：一条能力 = **代码** + **离线 gate** + **（可选）live gate** + **backlog `[x]`** + **demo 表一行**。

---

## 1. 门禁分层

| 层 | 命令 | 何时跑 | 需要 Key |
|----|------|--------|----------|
| L1 单测 | `pytest tests/test_*.py` | 每次 PR | 否 |
| L2 离线 smoke | `eval/*_gate.py run` | PR + `ci.yml` push | 否 |
| L3 Live | `eval/live_gate.py run` | 本地 / `live-gate.yml` | 是 |
| L4 演示 | `./eval/platform_demo.sh` | 面试前 | `--with-llm` 要 |

### 统一入口

```bash
# 离线（CI 同款）
python eval/agent_jd2_gate.py run
python eval/multimodal_embedding_gate.py run

# Live（无 Key → skip exit 0）
./eval/live_gate.sh
./eval/live_gate.sh --require-live   # 严格：无 Key 或失败 → exit 1

# GitHub Actions：Settings → Secrets → LLM_API_KEY → Actions → Live Gate → Run
```

---

## 2. Phase 收尾清单

1. 实现代码 + 单测  
2. 新增或扩展 `eval/*_smoke.py` / `*_gate.py`  
3. 接入 `.github/workflows/eval.yml` 或 `ci.yml`  
4. `issues-backlog-phase-*.md` 验收项 `[x]`  
5. `phase-*.md` §验证矩阵  
6. `demo-walkthrough.md` Live 表（live 手验后改 ✅）  
7. `roadmap.md` 状态 ✅ + tag  
8. 可选：`interview-narrative.md` 一句话  

---

## 3. 文档归档规则

| 文件 | 规则 |
|------|------|
| `issues-backlog.md` | **历史模板**；内嵌 `[ ]` 不代表未完成 |
| `issues-backlog-phase-*.md` | 以顶部 **状态表** 为准；Phase 完成后在文首标 ✅ |
| `PROJECT_STATUS.md` | **每个 tag 后**更新一次 |
| `tmp-jd-platform-comparison.md` | JD 对齐权威来源 |

---

## 4. Live 自动化说明

Live **可以**自动化，但无法在默认 CI 无 Secret 时强制通过：

- **本地**：`./eval/live_gate.sh --require-live`（Gateway + `.env`）  
- **CI**：`.github/workflows/live-gate.yml` + `LLM_API_KEY` secret + `workflow_dispatch`  
- **无 Key**：检查项标 `blocked`，`exit 0`（不挡 merge）  

这与业界实践一致：offline gate 挡回归，live gate 挡发布/手验。

---

## 5. 当前门禁一览

| Gate | 文件 | CI push | CI PR |
|------|------|---------|-------|
| acceptance | `acceptance_smoke.py` | ✅ | — |
| Agent JD2 | `agent_jd2_gate.py` | ✅ | ✅ |
| Multimodal | `multimodal_embedding_gate.py` | ✅ | ✅ |
| Live 统一 | `live_gate.py` | — | workflow_dispatch |
