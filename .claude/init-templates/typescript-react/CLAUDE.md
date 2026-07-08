# <PROJECT_NAME> — Project Instructions

## Role
<项目一句话定位>

## Build & Run

```bash
# 安装依赖
npm install

# 开发模式
npm run dev

# 生产构建
npm run build
```

## Test

```bash
# 单元测试
npx vitest run

# 带 UI
npx vitest --ui

# E2E (如适用)
npx playwright test
```

## Code Standards

- **Language**: TypeScript 5.x, strict mode
- **Style**: ESLint + Prettier. Run `npm run lint && npm run format:check`
- **Type Checking**: `tsc --noEmit` must pass before PR
- **Process**: Issue → feature branch → PR → merge. No direct pushes to `main`.

## Environment

| Variable | Purpose |
|----------|---------|
| `(请补充)` | |
