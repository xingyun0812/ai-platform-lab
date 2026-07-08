# <PROJECT_NAME> — Project Instructions

## Role
<项目一句话定位>

## Build & Run

```bash
# 编译
cargo build

# 运行开发模式
cargo run

# 发布构建
cargo build --release
```

## Test

```bash
# 全量测试
cargo test

# 带覆盖率 (需要 cargo-llvm-cov)
cargo llvm-cov --html
```

## Code Standards

- **Language**: Rust edition 2024
- **Style**: `cargo fmt` + `cargo clippy` — must pass before PR
- **Audit**: `cargo audit` for dependency vulnerabilities
- **Process**: Issue → feature branch → PR → merge. No direct pushes to `main`.

## Environment

| Variable | Purpose |
|----------|---------|
| `(请补充)` | |
