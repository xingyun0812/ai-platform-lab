# Phase O #92 — sql_query 只读工具

> **Issue**：[#92](https://github.com/xingyun0812/ai-platform-lab/issues/92)  
> **状态**：✅ 已交付

## 是什么

Agent 工具 **`sql_query`**：受控 **只读 SQL**，仅允许 `SELECT`，自动强制 `LIMIT`，拒绝 DML/DDL。

## 配置

| 环境变量 | 默认 | 说明 |
|----------|------|------|
| `SQL_QUERY_MODE` | `mock` | `mock` \| `postgres` |
| `SQL_AGENT_DATABASE_URL` | 空 | postgres 只读连接 |
| `SQL_QUERY_MAX_ROWS` | `100` | LIMIT 上限 |
| `SQL_QUERY_TIMEOUT_SECONDS` | `10` | 查询超时 |

## Seed 数据

[`samples/analytics_demo.sql`](../samples/analytics_demo.sql) — `demo_sales` 表示例数据（Postgres 手动 seed）。

mock 模式内置相同结构的假数据，CI 无需数据库。

## 安全

- 关键字拦截：`INSERT/UPDATE/DELETE/DROP/...`
- 拒绝多语句、`SELECT INTO`、`FOR UPDATE`
- 写操作 → `AGENT_TOOL_FORBIDDEN`（Runner 向上抛出 403）

## 验证

```bash
python -m unittest tests.test_tools_sql_query -v
```
