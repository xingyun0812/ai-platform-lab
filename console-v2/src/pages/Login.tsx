import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Form, Input, Button, Card, Typography, message, Space } from "antd";
import { UserOutlined, KeyOutlined, ExperimentOutlined } from "@ant-design/icons";
import apiClient from "../api/client";

const { Title, Text } = Typography;

interface LoginForm {
  tenant_id: string;
  api_key: string;
}

interface LoginResponse {
  token: string;
  tenant_id: string;
  role: string;
  expires_at?: string;
}

export default function Login() {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (values: LoginForm) => {
    setLoading(true);
    try {
      // Attempt JWT auth via /internal/auth/token
      const response = await apiClient.post<LoginResponse>("/internal/auth/token", {
        tenant_id: values.tenant_id,
        api_key: values.api_key,
      });

      const { token, tenant_id } = response.data;
      localStorage.setItem("token", token);
      localStorage.setItem("tenant_id", tenant_id);
      message.success("登录成功");
      navigate("/dashboard");
    } catch {
      // Fallback: store api_key directly as bearer token (for development)
      localStorage.setItem("token", values.api_key);
      localStorage.setItem("tenant_id", values.tenant_id);
      message.info("已保存凭据（开发模式）");
      navigate("/dashboard");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div
      style={{
        minHeight: "100vh",
        background: "#0d1117",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <Card
        style={{
          width: 400,
          background: "#161b22",
          border: "1px solid #30363d",
          borderRadius: 12,
        }}
      >
        <Space direction="vertical" size="large" style={{ width: "100%" }}>
          <div style={{ textAlign: "center" }}>
            <ExperimentOutlined style={{ fontSize: 40, color: "#1677ff" }} />
            <Title level={3} style={{ color: "#e6edf3", marginTop: 8 }}>
              AI Platform Lab
            </Title>
            <Text style={{ color: "#8b949e" }}>管理控制台 V2</Text>
          </div>

          <Form<LoginForm>
            layout="vertical"
            onFinish={handleSubmit}
            initialValues={{ tenant_id: "admin" }}
          >
            <Form.Item
              label={<span style={{ color: "#c9d1d9" }}>租户 ID</span>}
              name="tenant_id"
              rules={[{ required: true, message: "请输入租户 ID" }]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder="例如: admin"
                size="large"
                data-testid="tenant-id-input"
              />
            </Form.Item>

            <Form.Item
              label={<span style={{ color: "#c9d1d9" }}>API Key</span>}
              name="api_key"
              rules={[{ required: true, message: "请输入 API Key" }]}
            >
              <Input.Password
                prefix={<KeyOutlined />}
                placeholder="sk-tenant-..."
                size="large"
                data-testid="api-key-input"
              />
            </Form.Item>

            <Form.Item style={{ marginBottom: 0 }}>
              <Button
                type="primary"
                htmlType="submit"
                size="large"
                loading={loading}
                block
                data-testid="login-submit"
              >
                登录
              </Button>
            </Form.Item>
          </Form>

          <Text style={{ color: "#8b949e", fontSize: 12, textAlign: "center", display: "block" }}>
            默认管理员: admin / sk-tenant-admin-change-me
          </Text>
        </Space>
      </Card>
    </div>
  );
}
