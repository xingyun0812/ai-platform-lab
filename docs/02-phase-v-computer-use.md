# Phase V — Computer Use Agent

> **状态**：✅ **已交付**（Phase V · 2026-08-06）
> **前置**：Phase S（ToT）· Phase T（Debate）· Phase U（Deep Research）· Sandbox
> **门禁**：`python -m pytest tests/test_computer_use.py -v`

---

## 1. 动机

Phase S～U 交付了 ToT、Debate、Deep Research，但所有 Agent 都只能调用 API 工具。Computer Use 让 Agent 能操作 GUI 界面，这是 2025-2026 面试最高价值方向之一。

## 2. 架构

```
run_computer_use(task, config)
  │
  ├─ 循环（最多 max_steps 步）:
  │   ├─ 1. screenshot() → 当前屏幕截图（base64）
  │   ├─ 2. LLM 分析截图 + 任务 → 决定下一步动作
  │   │     动作类型: click(x,y) | type(text) | key(key) | scroll(dx,dy) | move(x,y) | done(answer)
  │   ├─ 3. 执行动作
  │   └─ 4. 检查是否完成
  │
  └─ 返回最终结果
```

## 3. API

```bash
curl -s http://127.0.0.1:8000/v1/agent/computer-use \
  -H "Content-Type: application/json" \
  -H "X-Tenant-Id: admin" \
  -H "Authorization: Bearer sk-tenant-admin-change-me" \
  -d '{
    "tenant_id": "admin",
    "session_id": "cu-demo",
    "goal": "打开计算器并计算 1+1"
  }'
```

## 4. 核心文件

| 路径 | 职责 |
|------|------|
| `packages/agent/computer_use/__init__.py` | `run_computer_use()` 编排器 |
| `packages/agent/computer_use/models.py` | 数据模型 |
| `packages/agent/computer_use/executor.py` | 动作执行（截图/点击/输入/按键/滚动） |
| `packages/agent/computer_use/planner.py` | LLM 截图分析 + 动作规划 |
| `packages/agent/tools/computer_use.py` | 工具 handler |
| `apps/gateway/agent/computer_use_routes.py` | `POST /v1/agent/computer-use` |

## 5. 执行方案

| 操作 | 实现 | 回退 |
|------|------|------|
| screenshot | `mss` → `pyautogui` → mock | PIL 生成空白图 |
| click | `pyautogui.click(x,y)` | log only |
| type | `pyautogui.write(text)` | log only |
| key | `pyautogui.press(key)` | log only |
| scroll | `pyautogui.scroll(dy)` | log only |
| move | `pyautogui.moveTo(x,y)` | log only |

## 6. 已知限制

| 限制 | 后续改进 |
|------|---------|
| mock 模式只记录日志 | 集成 Docker 沙箱 + Xvfb |
| 坐标归一化为 0-1000 | 自动映射到实际分辨率 |
| 不支持浏览器内操作 | 集成 Playwright/Selenium |
