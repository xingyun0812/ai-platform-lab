import React, { Suspense, lazy } from "react";
import { Routes, Route, Navigate, Link, useLocation } from "react-router-dom";
import { Layout, Menu, Spin, Typography } from "antd";
import {
  DashboardOutlined,
  TeamOutlined,
  RobotOutlined,
  DatabaseOutlined,
  BranchesOutlined,
  AuditOutlined,
  SettingOutlined,
  CloudServerOutlined,
  LogoutOutlined,
  ExperimentOutlined,
  ApiOutlined,
} from "@ant-design/icons";
import RequireAuth from "./components/RequireAuth";

const { Header, Sider, Content } = Layout;
const { Title } = Typography;

// Lazy-loaded pages
const Login = lazy(() => import("./pages/Login"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Tenants = lazy(() => import("./pages/Tenants"));
const Agents = lazy(() => import("./pages/Agents"));
const RAG = lazy(() => import("./pages/RAG"));
const Memory = lazy(() => import("./pages/Memory"));
const Orchestrator = lazy(() => import("./pages/Orchestrator"));
const Audit = lazy(() => import("./pages/Audit"));
const Settings = lazy(() => import("./pages/Settings"));
const Embedding = lazy(() => import("./pages/Embedding"));

const menuItems = [
  { key: "/dashboard", icon: <DashboardOutlined />, label: <Link to="/dashboard">Dashboard</Link> },
  { key: "/tenants", icon: <TeamOutlined />, label: <Link to="/tenants">租户管理</Link> },
  { key: "/agents", icon: <RobotOutlined />, label: <Link to="/agents">Agent 管理</Link> },
  { key: "/rag", icon: <DatabaseOutlined />, label: <Link to="/rag">RAG 知识库</Link> },
  { key: "/embedding", icon: <ApiOutlined />, label: <Link to="/embedding">Embedding</Link> },
  { key: "/memory", icon: <CloudServerOutlined />, label: <Link to="/memory">记忆管理</Link> },
  { key: "/orchestrator", icon: <BranchesOutlined />, label: <Link to="/orchestrator">工作流编排</Link> },
  { key: "/audit", icon: <AuditOutlined />, label: <Link to="/audit">审计日志</Link> },
  { key: "/settings", icon: <SettingOutlined />, label: <Link to="/settings">系统设置</Link> },
];

function AppLayout() {
  const location = useLocation();
  const selectedKey = "/" + location.pathname.split("/")[1];

  const handleLogout = () => {
    localStorage.removeItem("token");
    localStorage.removeItem("tenant_id");
    window.location.href = `${import.meta.env.BASE_URL.replace(/\/$/, "") || "/console"}/login`;
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        width={220}
        style={{
          background: "#141414",
          borderRight: "1px solid #2a2a2a",
          position: "fixed",
          height: "100vh",
          left: 0,
          top: 0,
          zIndex: 100,
        }}
      >
        <div
          style={{
            padding: "16px",
            borderBottom: "1px solid #2a2a2a",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <ExperimentOutlined style={{ fontSize: 20, color: "#1677ff" }} />
          <Title level={5} style={{ margin: 0, color: "#e6edf3", fontSize: 14 }}>
            AI Platform Lab
          </Title>
        </div>
        <Menu
          mode="inline"
          selectedKeys={[selectedKey]}
          style={{ background: "transparent", border: "none", marginTop: 8 }}
          theme="dark"
          items={menuItems}
        />
        <div
          style={{
            position: "absolute",
            bottom: 16,
            left: 0,
            right: 0,
            padding: "0 16px",
          }}
        >
          <Menu
            mode="inline"
            theme="dark"
            style={{ background: "transparent", border: "none" }}
            items={[
              {
                key: "logout",
                icon: <LogoutOutlined />,
                label: "退出登录",
                onClick: handleLogout,
              },
            ]}
          />
        </div>
      </Sider>

      <Layout style={{ marginLeft: 220 }}>
        <Header
          style={{
            background: "#141414",
            borderBottom: "1px solid #2a2a2a",
            padding: "0 24px",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
          }}
        >
          <span style={{ color: "#8b949e", fontSize: 13 }}>
            {menuItems.find((m) => m.key === selectedKey)
              ? String(menuItems.find((m) => m.key === selectedKey)?.label ?? "")
              : "控制台"}
          </span>
          <span style={{ color: "#8b949e", fontSize: 12 }}>
            {localStorage.getItem("tenant_id") || "未登录"}
          </span>
        </Header>
        <Content
          style={{
            background: "#0d1117",
            minHeight: "calc(100vh - 64px)",
            padding: 0,
          }}
        >
          <Suspense
            fallback={
              <div style={{ display: "flex", justifyContent: "center", padding: 80 }}>
                <Spin size="large" />
              </div>
            }
          >
            <Routes>
              <Route path="/" element={<Navigate to="/dashboard" replace />} />
              <Route path="/dashboard" element={<Dashboard />} />
              <Route path="/tenants" element={<Tenants />} />
              <Route path="/agents" element={<Agents />} />
              <Route path="/rag" element={<RAG />} />
              <Route path="/embedding" element={<Embedding />} />
              <Route path="/memory" element={<Memory />} />
              <Route path="/orchestrator" element={<Orchestrator />} />
              <Route path="/audit" element={<Audit />} />
              <Route path="/settings" element={<Settings />} />
            </Routes>
          </Suspense>
        </Content>
      </Layout>
    </Layout>
  );
}

export default function App() {
  return (
    <Suspense fallback={<Spin fullscreen />}>
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route
          path="/*"
          element={
            <RequireAuth>
              <AppLayout />
            </RequireAuth>
          }
        />
      </Routes>
    </Suspense>
  );
}
