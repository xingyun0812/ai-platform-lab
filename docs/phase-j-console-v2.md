# Phase J — Console V2：React 管理界面（#30 / roadmap #46）

> **目标**：用 React + Vite + TypeScript + Ant Design 替换 `apps/console/index.html` 的 HTML stub，打造完整的管理控制台，覆盖所有 Backend REST API。

---

## 1. 设计要点

构建思路、使用链路与逐文件代码说明见 [phase-j-build-and-code-guide.md](./phase-j-build-and-code-guide.md)。

### 1.1 技术栈

| 层次 | 技术 | 版本 | 说明 |
|------|------|------|------|
| 框架 | React | 18 | Concurrent mode + Suspense |
| 构建工具 | Vite | 5 | 极速 HMR，生产 Tree-shaking |
| 语言 | TypeScript | 5 | 严格模式 (`strict: true`) |
| UI 组件 | Ant Design | 5 | 暗色主题 (`darkAlgorithm`) |
| 路由 | react-router-dom | 6 | 声明式路由 + lazy loading |
| 数据获取 | @tanstack/react-query | 5 | 缓存 + 自动刷新 + Loading 状态 |
| HTTP | axios | 1 | 拦截器注入 JWT + X-Tenant-Id |
| 图表 | recharts | 2 | 折线图（QPS）+ 饼图（Token 分布）|
| 测试 | vitest + @testing-library/react | 1 / 16 | jsdom 环境，≥12 用例 |

### 1.2 项目结构

```
console-v2/
├── index.html                  # Vite 入口 HTML
├── package.json                # 依赖声明
├── vite.config.ts              # Vite + Vitest 配置
├── tsconfig.json               # TypeScript 编译配置
├── tsconfig.node.json          # Vite 配置的 tsconfig
├── vitest.config.ts            # 独立 Vitest 配置
├── src/
│   ├── main.tsx                # ReactDOM.createRoot + Provider 包裹
│   ├── App.tsx                 # 路由树 + AppLayout
│   ├── index.css               # 全局样式
│   ├── api/
│   │   ├── client.ts           # Axios 实例 + 拦截器
│   │   ├── chat.ts             # /v1/chat/completions
│   │   ├── rag.ts              # /internal/rag/*
│   │   ├── agent.ts            # /internal/agents/*
│   │   ├── embedding.ts        # /v1/embeddings
│   │   ├── memory.ts           # /internal/memory/*
│   │   ├── orchestrator.ts     # /internal/orchestrator/*
│   │   └── audit.ts            # /internal/audit/*
│   ├── pages/
│   │   ├── Login.tsx           # JWT 登录表单
│   │   ├── Dashboard.tsx       # QPS/Token/错误率/活跃会话 + 图表
│   │   ├── Tenants.tsx         # 租户 CRUD（admin only）
│   │   ├── Agents.tsx          # Agent 管理 + 委托 + 版本历史
│   │   ├── RAG.tsx             # 知识库 + 文档上传 + 查询测试
│   │   ├── Memory.tsx          # 记忆搜索 + 删除
│   │   ├── Orchestrator.tsx    # 工作流 CRUD + JSON 编辑器 + 执行
│   │   ├── Audit.tsx           # 审计日志 + 工具分类管理
│   │   └── Settings.tsx        # 功能开关只读视图
│   └── components/
│       └── RequireAuth.tsx     # 认证守卫 HOC
└── tests/
    ├── setup.ts                # Jest-dom + 浏览器 Mock
    └── App.test.tsx            # ≥12 测试用例
```

### 1.3 认证流程

```
用户输入 tenant_id + api_key
         ↓
POST /internal/auth/token
         ↓ (200 OK)
{ token, tenant_id, role }
         ↓
localStorage.setItem("token", token)
localStorage.setItem("tenant_id", tenant_id)
         ↓
RequireAuth HOC 检查 localStorage
  → 有 token → 渲染页面
  → 无 token → Navigate to /login
         ↓
每次 API 请求：
  Axios 拦截器注入:
    Authorization: Bearer <token>
    X-Tenant-Id: <tenant_id>
         ↓
  401 响应 → 清除 localStorage → 跳转 /login
```

### 1.4 构建与部署

```bash
# 开发（代理到 localhost:8000）
cd console-v2
npm install
npm run dev    # → http://localhost:3000

# 生产构建（输出到 FastAPI 静态目录）
npm run build  # → apps/console/static/index.html

# FastAPI 挂载（在 main.py 中由 parent agent 集成）
app.mount("/console", StaticFiles(directory="apps/console/static", html=True), name="console")
```

---

## 2. 数据模型（TypeScript）

### 2.1 认证

```typescript
interface LoginResponse {
  token: string;
  tenant_id: string;
  role: "platform_admin" | "tenant_user" | "read_only";
  expires_at?: string;
}
```

### 2.2 租户

```typescript
interface TenantRecord {
  tenant_id: string;
  role: "platform_admin" | "tenant_user" | "read_only";
  quota_tokens_per_month: number;
  tokens_used_this_month: number;
  enabled: boolean;
  created_at: string;
}
```

### 2.3 Agent

```typescript
interface AgentSpec {
  agent_id: string;
  name: string;
  role: "primary" | "specialist" | "reviewer" | "router";
  description: string;
  system_prompt: string;
  model?: string;
  allowed_tools: string[];
  can_delegate: boolean;
  can_be_delegated_to: boolean;
  max_delegation_depth: number;
  enabled: boolean;
}
```

### 2.4 工作流

```typescript
interface WorkflowDefinition {
  workflow_id: string;
  name: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  enabled: boolean;
}
```

---

## 3. REST API 集成表

| 页面 | API 端点 | 方法 | 说明 |
|------|----------|------|------|
| Login | `/internal/auth/token` | POST | JWT 获取 |
| Dashboard | `/internal/metrics` | GET | QPS/Token/错误率 |
| Dashboard | `/internal/billing/usage` | GET | Token 消耗 |
| Tenants | `/internal/tenants` | GET/POST | 列出 / 创建 |
| Tenants | `/internal/tenants/{id}` | PATCH/DELETE | 更新 / 删除 |
| Agents | `/internal/agents` | GET/POST | 列出 / 创建 |
| Agents | `/internal/agents/{id}` | PATCH/DELETE | 更新 / 删除 |
| Agents | `/internal/agents/{id}/delegate` | POST | 委托任务 |
| Agents | `/internal/agents/{id}/versions` | GET | 版本历史 |
| RAG | `/internal/rag/knowledge-bases` | GET/POST | 知识库 |
| RAG | `/internal/rag/knowledge-bases/{id}/documents` | GET/POST | 文档上传 |
| RAG | `/internal/rag/query` | POST | 语义查询测试 |
| Memory | `/internal/memory` | GET/POST | 记忆搜索 / 创建 |
| Memory | `/internal/memory/{id}` | DELETE | 删除记忆 |
| Orchestrator | `/internal/orchestrator/workflows` | GET/POST | 工作流 |
| Orchestrator | `/internal/orchestrator/workflows/{id}/execute` | POST | 执行 |
| Audit | `/internal/audit` | GET | 审计日志列表 |
| Audit | `/internal/audit/tools` | GET/PUT | 工具分类 |
| Settings | `/internal/settings` | GET | 功能开关（只读）|

---

## 4. 配置说明（无 settings.py 变更）

Console V2 是静态前端，**不需要修改 `settings.py`**。配置通过 Vite 环境变量在构建时注入：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `VITE_API_BASE` | `/` | API 基础 URL（可改为 `https://api.example.com`）|

在 `console-v2/.env.local`（不提交 git）中设置：
```env
VITE_API_BASE=http://localhost:8000
```

---

## 5. 共享文件集成说明（供 parent agent 操作）

### 5.1 main.py 集成

在 `apps/gateway/main.py` 末尾 **或** `app` 创建后添加：

```python
from fastapi.staticfiles import StaticFiles
from pathlib import Path

_CONSOLE_STATIC = Path(__file__).parent.parent / "apps" / "console" / "static"
if _CONSOLE_STATIC.exists():
    app.mount("/console", StaticFiles(directory=str(_CONSOLE_STATIC), html=True), name="console")
```

### 5.2 .env.example 新增内容

无需新增（前端配置在 `console-v2/.env.local`）。

### 5.3 README.md 新增节

```markdown
## Console V2 — React 管理界面

```bash
cd console-v2
npm install
npm run dev       # 开发服务器 http://localhost:3000
npm run build     # 生产构建 → apps/console/static/
npm test          # 运行 Vitest 测试
```

构建后访问：`http://localhost:8000/console/`
```

### 5.4 roadmap.md 更新

在 Phase J 行中更新状态：`#46 Console V2 React 管理界面 ✅`

---

## 6. 测试说明

### 6.1 TypeScript/Vitest 测试

```bash
cd console-v2
npm install
npm test
# → 运行 tests/App.test.tsx（12 个用例）
```

测试覆盖：
| # | 测试 | 验证点 |
|---|------|--------|
| 1 | Login 页面渲染 | tenant-id-input / api-key-input / submit 按钮存在 |
| 2 | Login 提交存 token | localStorage.setItem 被调用 |
| 3 | Dashboard 渲染统计卡 | data-testid="dashboard" 存在 |
| 4 | Dashboard API 报错回退 | 显示 mock 数据 |
| 5 | Tenants 页面容器 | data-testid="tenants-page" |
| 6 | Agents 页面创建按钮 | create-agent-btn 存在 |
| 7 | Memory 搜索过滤器 | memory-search-input 存在 |
| 8 | Orchestrator JSON 编辑器 | workflow-json-editor 在 modal 中 |
| 9 | Audit 过滤下拉 | action-level-filter 存在 |
| 10 | RequireAuth 无 token 重定向 | 渲染 login-page |
| 11 | RequireAuth 有 token 渲染子组件 | protected-content 可见 |
| 12 | Settings 功能开关卡片 | feature-flags-card 存在 |

### 6.2 Python 冒烟测试

```bash
cd /Users/liuli/Downloads/ai-platform-lab
python3 tests/test_console_v2.py
# → 12 个检查，全部 pass（前提：未运行 build，test_7 会检查 static/ 目录）
```

---

## 7. 代码导航

| 文件 | 关键代码 |
|------|----------|
| `console-v2/src/main.tsx` | `ReactDOM.createRoot` + 三层 Provider（BrowserRouter / QueryClient / ConfigProvider）|
| `console-v2/src/App.tsx` | `AppLayout` Sider 导航 + `RequireAuth` 包裹 + `React.lazy` 懒加载 |
| `console-v2/src/api/client.ts` | `axios.interceptors.request` 注入 JWT；`interceptors.response` 处理 401 |
| `console-v2/src/pages/Dashboard.tsx` | `useQuery` → `/internal/metrics`；`recharts` 折线图 + 饼图 |
| `console-v2/src/pages/Agents.tsx` | 委托 Dialog (`useMutation` + `agentApi.delegate`)；版本历史 Drawer |
| `console-v2/src/pages/RAG.tsx` | Ant Design `Dragger` 上传；`ragApi.query` 查询测试面板 |
| `console-v2/src/pages/Orchestrator.tsx` | `TextArea` JSON 编辑器 + 实时语法校验 + 执行结果展示 |
| `console-v2/src/components/RequireAuth.tsx` | `localStorage.getItem("token")` + `<Navigate to="/login">` |
| `console-v2/vite.config.ts` | `build.outDir: "../apps/console/static"` + dev proxy → `:8000` |
| `console-v2/tests/App.test.tsx` | 12 用例，`vi.mock("../src/api/client")` 隔离网络 |

---

## 8. 已知限制

1. **无 SSR（Server-Side Rendering）**：为 SPA（单页应用），首屏 HTML 无服务端渲染内容，SEO 受限，首屏白屏时间取决于 JS 包大小。
2. **无国际化（i18n）**：界面文本硬编码中文，切换语言需重新构建或引入 `react-i18next`。
3. **无 PWA / 离线支持**：无 Service Worker，断网后页面不可用；轮询模式（`refetchInterval`）非实时推送。
4. **无移动端响应式优化**：仅针对桌面端 ≥1280px 设计，Ant Design 响应式断点未精细调整，小屏体验较差。
5. **无 WebSocket 实时更新**：Dashboard 指标通过 30s 轮询更新，非 WebSocket/SSE 推送，延迟较高。
6. **无错误边界（Error Boundary）**：页面级别未包裹 `<ErrorBoundary>`，子组件异常可能导致白屏（Suspense 的 fallback 仅处理加载态）。
7. **无单元测试覆盖率要求**：当前 12 用例主要测试挂载和关键元素存在性，未测试用户交互完整流程（缺少 E2E）。
8. **构建依赖外部 npm registry**：CI 需要可访问 npm 或配置镜像。

---

## 9. 面试要点（Interview Talking Points）

### 9.1 为什么选择 React Query（@tanstack/react-query）而非 Redux？

> **答**：管理后台的核心数据获取模式是「服务端状态」（Server State），特点是异步、可过时、需要缓存和重新验证。React Query 专门为此场景设计，提供开箱即用的 loading/error 状态、缓存策略（`staleTime`）、后台重新获取（`refetchInterval`）。相比 Redux，无需编写 action/reducer/selector 样板代码，代码量减少 60% 以上。

### 9.2 如何保证 JWT Token 安全？

> **答**：当前实现将 Token 存储在 `localStorage`，对 XSS 攻击存在一定风险。生产建议方案：
> - **HttpOnly Cookie**：Token 存在服务器设置的 HttpOnly Cookie 中，JS 无法读取，防 XSS
> - **短期 Token + Refresh Token**：Access Token 5 分钟过期，通过 Refresh Token 静默续期
> - **CSP 头部**：配置 Content-Security-Policy 减少 XSS 攻击面

### 9.3 Vite 为什么比 webpack 快？

> **答**：Vite 开发模式利用浏览器原生 ESM，按需编译模块（不打包），实现毫秒级 HMR。生产构建使用 Rollup，Tree-shaking 精准。而 webpack 每次 HMR 需要重新打包整个 bundle，项目越大越慢。Vite 还支持 `manualChunks` 代码分割，将 antd/recharts 等重库提取为独立 chunk，首屏加载只拉取核心 JS。

### 9.4 React.lazy + Suspense 的作用？

> **答**：页面组件通过 `React.lazy(() => import("./pages/Dashboard"))` 实现代码分割，每个页面只在用户首次访问时才下载对应的 JS chunk。`<Suspense fallback={<Spin />}>` 在 chunk 加载期间显示 Loading 状态。这减少了首屏 JS 体积，Dashboard 页的 recharts 依赖（~500KB）只在访问 Dashboard 时加载。

### 9.5 如何测试 Axios 拦截器？

> **答**：在 Vitest 中通过 `vi.mock("../src/api/client")` 将整个 axios 实例替换为 mock 对象，各测试用例通过 `mockResolvedValue`/`mockRejectedValue` 控制返回。拦截器本身通过集成测试验证：创建真实 axios 实例，用 `axios-mock-adapter` 拦截请求，断言请求头中包含正确的 Authorization 和 X-Tenant-Id。

### 9.6 如何处理管理员权限的前端控制？

> **答**：前端权限控制采用双层防护：
> 1. **UI 层**：从 localStorage 读取 tenant_id，如果不是 admin role 则隐藏「新增租户」等按钮
> 2. **API 层**：后端 `can_patch_tenant_limits(role)` 函数真正鉴权，前端隐藏仅是 UX 优化
>
> 前端权限控制**不能替代**后端鉴权，它只是防止普通用户误操作；攻击者仍可直接调用 API，后端必须独立验证权限。

### 9.7 为什么 build outDir 指向 `../apps/console/static`？

> **答**：FastAPI 通过 `StaticFiles(directory="apps/console/static", html=True)` 托管前端构建产物，挂载路径 `/console`。这样整个服务只需部署一个 Docker 镜像（Python + 静态文件），无需单独的 Nginx 或 CDN。`html=True` 参数让 FastAPI 对所有未匹配路径返回 `index.html`，支持 React Router 的 `BrowserRouter`（客户端路由）。
