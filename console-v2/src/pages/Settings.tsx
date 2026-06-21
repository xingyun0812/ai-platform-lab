import React from "react";
import { Card, Descriptions, Tag, Typography, Spin, Alert, Row, Col, Switch } from "antd";
import { useQuery } from "@tanstack/react-query";
import apiClient from "../api/client";

const { Title, Text } = Typography;

interface PlatformSettings {
  sandbox_enabled: boolean;
  oauth2_enabled: boolean;
  mtls_enabled: boolean;
  pii_enabled: boolean;
  audit_enabled: boolean;
  memory_enabled: boolean;
  rag_enabled: boolean;
  embedding_enabled: boolean;
  orchestrator_enabled: boolean;
  multi_agent_enabled: boolean;
  default_model: string;
  max_tokens_per_request: number;
  rate_limit_per_minute: number;
  version: string;
}

function useSettings() {
  return useQuery<PlatformSettings>({
    queryKey: ["settings"],
    queryFn: async () => {
      try {
        const res = await apiClient.get("/internal/settings");
        return res.data;
      } catch {
        // Return defaults for display
        return {
          sandbox_enabled: false,
          oauth2_enabled: false,
          mtls_enabled: false,
          pii_enabled: true,
          audit_enabled: true,
          memory_enabled: true,
          rag_enabled: true,
          embedding_enabled: true,
          orchestrator_enabled: true,
          multi_agent_enabled: true,
          default_model: "gpt-4o-mini",
          max_tokens_per_request: 4096,
          rate_limit_per_minute: 60,
          version: "unknown",
        } as PlatformSettings;
      }
    },
    staleTime: 60_000,
  });
}

function FeatureFlag({ label, enabled }: { label: string; enabled: boolean }) {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "center",
        padding: "8px 0",
        borderBottom: "1px solid #21262d",
      }}
    >
      <Text style={{ color: "#c9d1d9" }}>{label}</Text>
      <Tag color={enabled ? "success" : "default"}>{enabled ? "已启用" : "已禁用"}</Tag>
    </div>
  );
}

export default function Settings() {
  const { data, isLoading } = useSettings();

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: 80 }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <div style={{ padding: 24 }} data-testid="settings-page">
      <Title level={4} style={{ color: "#e6edf3", marginBottom: 8 }}>
        系统设置
      </Title>
      <Alert
        message="此页面为只读视图。若需修改配置，请编辑 .env 文件并重启服务。"
        type="info"
        showIcon
        style={{ marginBottom: 24 }}
      />

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <Card
            title={<span style={{ color: "#c9d1d9" }}>功能开关</span>}
            style={{ background: "#161b22", border: "1px solid #30363d" }}
            data-testid="feature-flags-card"
          >
            <FeatureFlag label="沙箱模式 (Sandbox)" enabled={data?.sandbox_enabled ?? false} />
            <FeatureFlag label="OAuth2 认证" enabled={data?.oauth2_enabled ?? false} />
            <FeatureFlag label="mTLS 双向证书" enabled={data?.mtls_enabled ?? false} />
            <FeatureFlag label="PII 脱敏检测" enabled={data?.pii_enabled ?? true} />
            <FeatureFlag label="审计日志" enabled={data?.audit_enabled ?? true} />
            <FeatureFlag label="记忆系统" enabled={data?.memory_enabled ?? true} />
            <FeatureFlag label="RAG 知识库" enabled={data?.rag_enabled ?? true} />
            <FeatureFlag label="嵌入服务" enabled={data?.embedding_enabled ?? true} />
            <FeatureFlag label="工作流编排" enabled={data?.orchestrator_enabled ?? true} />
            <FeatureFlag label="Multi-Agent" enabled={data?.multi_agent_enabled ?? true} />
          </Card>
        </Col>

        <Col xs={24} lg={12}>
          <Card
            title={<span style={{ color: "#c9d1d9" }}>平台参数</span>}
            style={{ background: "#161b22", border: "1px solid #30363d" }}
            data-testid="platform-params-card"
          >
            <Descriptions column={1} size="small">
              <Descriptions.Item label="默认模型">
                <code style={{ color: "#79c0ff" }}>{data?.default_model ?? "-"}</code>
              </Descriptions.Item>
              <Descriptions.Item label="单请求最大 Token">
                {data?.max_tokens_per_request?.toLocaleString() ?? "-"}
              </Descriptions.Item>
              <Descriptions.Item label="限速 (次/分钟)">
                {data?.rate_limit_per_minute ?? "-"}
              </Descriptions.Item>
              <Descriptions.Item label="服务版本">
                <Tag color="blue">{data?.version ?? "unknown"}</Tag>
              </Descriptions.Item>
            </Descriptions>
          </Card>

          <Card
            title={<span style={{ color: "#c9d1d9" }}>当前登录信息</span>}
            style={{ background: "#161b22", border: "1px solid #30363d", marginTop: 16 }}
            data-testid="login-info-card"
          >
            <Descriptions column={1} size="small">
              <Descriptions.Item label="租户 ID">
                <code style={{ color: "#79c0ff" }}>
                  {localStorage.getItem("tenant_id") ?? "未登录"}
                </code>
              </Descriptions.Item>
              <Descriptions.Item label="Token">
                <Text
                  style={{ color: "#8b949e", fontSize: 11 }}
                  copyable={{ text: localStorage.getItem("token") ?? "" }}
                >
                  {localStorage.getItem("token")
                    ? `${localStorage.getItem("token")?.substring(0, 20)}...`
                    : "无"}
                </Text>
              </Descriptions.Item>
            </Descriptions>
          </Card>
        </Col>
      </Row>
    </div>
  );
}
