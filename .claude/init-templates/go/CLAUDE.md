# <PROJECT_NAME> — Project Instructions

## Role
<项目一句话定位>

## Build & Run

```bash
# 编译
make build

# 运行
./bin/<project>

# 开发模式热重载（如用 air）
air
```

## Test

```bash
# 全量测试
go test ./... -race

# 带覆盖率
go test ./... -coverprofile=coverage.out && go tool cover -html=coverage.out -o coverage.html
```

## Code Standards

- **Language**: Go 1.22+
- **Style**: `gofumpt -l .` + `golangci-lint run`
- **Process**: Issue → feature branch → PR → merge. No direct pushes to `main`.

## Environment

| Variable | Purpose |
|----------|---------|
| `(请补充)` | |
