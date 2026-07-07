# ai-platform-lab Justfile
# 用法: just <recipe> [args]
# 首次运行 `just --init` 或直接执行

# ---------- 开发服务器 ----------

# 启动全栈 (docker compose)
up:
    docker compose up -d --build

# 启动 gateway 仅
up-gateway:
    uvicorn apps.gateway.main:app --host 127.0.0.1 --port 8000

# 停止 & 清理
clean:
    docker compose down -v


# ---------- 测试 ----------

# 运行单元测试
test:
    python -m pytest tests/ -q

# 运行单元测试 + 覆盖率
test-cov:
    python -m pytest tests/ --cov=packages --cov-report=term-missing -q

# 运行单个测试文件
# 用法: just test-file tests/test_xxx.py
test-file f:
    python -m pytest {{f}} -q -v


# ---------- Lint / Format ----------

# Ruff 检查
lint:
    ruff check .

# Ruff 格式化检查
fmt-check:
    ruff format --check .

# Ruff 格式化写入
fmt:
    ruff format .

# 类型检查 mypy (仅已覆盖模块)
type:
    mypy packages/platform/ packages/contracts/


# ---------- Eval 门禁 ----------

# 离线 eval（无 API key 时跳过 live 用例）
eval:
    python eval/run.py run-eval --sample-limit 20

# eval 门禁对比 baseline
gate:
    python eval/run.py gate --threshold 5

# smoke 测试（需要 stack 已启动）
smoke:
    python eval/acceptance_smoke.py

# agent_jd2 门禁
agate:
    python eval/agent_jd2_gate.py run

# multimodal 门禁
mgate:
    python eval/multimodal_embedding_gate.py run

# harness 门禁
hgate:
    python eval/harness_capability_gate.py run


# ---------- Setup ----------

# 安装项目 + 开发依赖
setup:
    pip install -e ".[dev]"

# 安装 pre-commit hooks
hooks:
    bash scripts/setup-hooks.sh


# ---------- Help ----------

# 列出所有命令
_default:
    @just --list
