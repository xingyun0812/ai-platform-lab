-- Phase O #92 — Agent sql_query 演示数据
-- 在 Postgres 中执行以 seed demo_sales 表（只读 Agent 工具查询此表）

CREATE TABLE IF NOT EXISTS demo_sales (
    id SERIAL PRIMARY KEY,
    region TEXT NOT NULL,
    product TEXT NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    quarter TEXT NOT NULL
);

TRUNCATE demo_sales RESTART IDENTITY;

INSERT INTO demo_sales (region, product, amount, quarter) VALUES
    ('CN', 'Widget A', 12000.00, '2024-Q1'),
    ('CN', 'Widget B',  8500.00, '2024-Q1'),
    ('US', 'Widget A', 15200.00, '2024-Q1'),
    ('EU', 'Widget C',  6300.00, '2024-Q2'),
    ('CN', 'Widget A', 14100.00, '2024-Q2');

-- 示例只读查询（Agent sql_query 工具）
-- SELECT region, SUM(amount) AS total FROM demo_sales GROUP BY region LIMIT 10;
