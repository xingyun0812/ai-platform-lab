import React from "react";
import { Row, Col, Card, Statistic, Typography, Spin, Alert } from "antd";
import {
  ArrowUpOutlined,
  ArrowDownOutlined,
  ThunderboltOutlined,
  DollarOutlined,
  ExclamationCircleOutlined,
  ApiOutlined,
} from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import {
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import apiClient from "../api/client";

const { Title } = Typography;

interface MetricsData {
  qps: number;
  qps_delta: number;
  tokens_today: number;
  error_rate: number;
  active_sessions: number;
  requests_timeline: Array<{ time: string; requests: number; errors: number }>;
  tokens_by_tenant: Array<{ tenant_id: string; tokens: number }>;
}

const CHART_COLORS = ["#1677ff", "#52c41a", "#faad14", "#f5222d", "#722ed1", "#13c2c2"];

function useMetrics() {
  return useQuery<MetricsData>({
    queryKey: ["metrics"],
    queryFn: async () => {
      try {
        const res = await apiClient.get("/internal/metrics");
        return res.data;
      } catch {
        // Return mock data for demo purposes
        return {
          qps: 42.5,
          qps_delta: 8.2,
          tokens_today: 1_280_000,
          error_rate: 0.3,
          active_sessions: 17,
          requests_timeline: Array.from({ length: 12 }, (_, i) => ({
            time: `${i * 2}:00`,
            requests: Math.floor(Math.random() * 500 + 100),
            errors: Math.floor(Math.random() * 20),
          })),
          tokens_by_tenant: [
            { tenant_id: "admin", tokens: 450_000 },
            { tenant_id: "tenant-a", tokens: 320_000 },
            { tenant_id: "tenant-b", tokens: 280_000 },
            { tenant_id: "tenant-c", tokens: 230_000 },
          ],
        };
      }
    },
    refetchInterval: 30_000,
  });
}

export default function Dashboard() {
  const { data, isLoading, error } = useMetrics();

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }} data-testid="dashboard">
      <Title level={4} style={{ color: "#e6edf3", marginBottom: 24 }}>
        平台总览
      </Title>

      {error && (
        <Alert
          message="指标加载失败，显示模拟数据"
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
        />
      )}

      {/* Stats Row */}
      <Row gutter={[16, 16]}>
        <Col xs={24} sm={12} xl={6}>
          <Card style={{ background: "#161b22", border: "1px solid #30363d" }} data-testid="stat-qps">
            <Statistic
              title={<span style={{ color: "#8b949e" }}>当前 QPS</span>}
              value={data?.qps ?? 0}
              precision={1}
              valueStyle={{ color: "#1677ff" }}
              prefix={<ThunderboltOutlined />}
              suffix={
                <span style={{ fontSize: 12, color: "#52c41a" }}>
                  <ArrowUpOutlined /> {data?.qps_delta?.toFixed(1)}%
                </span>
              }
            />
          </Card>
        </Col>

        <Col xs={24} sm={12} xl={6}>
          <Card style={{ background: "#161b22", border: "1px solid #30363d" }} data-testid="stat-tokens">
            <Statistic
              title={<span style={{ color: "#8b949e" }}>今日 Token 消耗</span>}
              value={data?.tokens_today ?? 0}
              valueStyle={{ color: "#52c41a" }}
              prefix={<DollarOutlined />}
              formatter={(val) =>
                Number(val) >= 1_000_000
                  ? `${(Number(val) / 1_000_000).toFixed(2)}M`
                  : Number(val) >= 1_000
                  ? `${(Number(val) / 1_000).toFixed(1)}K`
                  : String(val)
              }
            />
          </Card>
        </Col>

        <Col xs={24} sm={12} xl={6}>
          <Card style={{ background: "#161b22", border: "1px solid #30363d" }} data-testid="stat-error-rate">
            <Statistic
              title={<span style={{ color: "#8b949e" }}>错误率</span>}
              value={data?.error_rate ?? 0}
              precision={2}
              valueStyle={{
                color: (data?.error_rate ?? 0) > 1 ? "#f5222d" : "#52c41a",
              }}
              prefix={<ExclamationCircleOutlined />}
              suffix="%"
            />
          </Card>
        </Col>

        <Col xs={24} sm={12} xl={6}>
          <Card style={{ background: "#161b22", border: "1px solid #30363d" }} data-testid="stat-sessions">
            <Statistic
              title={<span style={{ color: "#8b949e" }}>活跃会话</span>}
              value={data?.active_sessions ?? 0}
              valueStyle={{ color: "#faad14" }}
              prefix={<ApiOutlined />}
            />
          </Card>
        </Col>
      </Row>

      {/* Charts Row */}
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} xl={16}>
          <Card
            title={<span style={{ color: "#c9d1d9" }}>请求趋势（近 24h）</span>}
            style={{ background: "#161b22", border: "1px solid #30363d" }}
            data-testid="chart-requests"
          >
            <ResponsiveContainer width="100%" height={250}>
              <LineChart data={data?.requests_timeline ?? []}>
                <CartesianGrid strokeDasharray="3 3" stroke="#30363d" />
                <XAxis dataKey="time" stroke="#8b949e" tick={{ fontSize: 11 }} />
                <YAxis stroke="#8b949e" tick={{ fontSize: 11 }} />
                <Tooltip
                  contentStyle={{ background: "#161b22", border: "1px solid #30363d" }}
                  labelStyle={{ color: "#c9d1d9" }}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="requests"
                  stroke="#1677ff"
                  strokeWidth={2}
                  dot={false}
                  name="请求数"
                />
                <Line
                  type="monotone"
                  dataKey="errors"
                  stroke="#f5222d"
                  strokeWidth={2}
                  dot={false}
                  name="错误数"
                />
              </LineChart>
            </ResponsiveContainer>
          </Card>
        </Col>

        <Col xs={24} xl={8}>
          <Card
            title={<span style={{ color: "#c9d1d9" }}>租户 Token 分布</span>}
            style={{ background: "#161b22", border: "1px solid #30363d" }}
            data-testid="chart-tokens-pie"
          >
            <ResponsiveContainer width="100%" height={250}>
              <PieChart>
                <Pie
                  data={data?.tokens_by_tenant ?? []}
                  dataKey="tokens"
                  nameKey="tenant_id"
                  cx="50%"
                  cy="50%"
                  outerRadius={80}
                  label={({ tenant_id, percent }) =>
                    `${tenant_id}: ${(percent * 100).toFixed(0)}%`
                  }
                >
                  {(data?.tokens_by_tenant ?? []).map((_, index) => (
                    <Cell key={index} fill={CHART_COLORS[index % CHART_COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{ background: "#161b22", border: "1px solid #30363d" }}
                />
              </PieChart>
            </ResponsiveContainer>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
