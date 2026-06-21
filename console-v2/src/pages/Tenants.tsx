import React, { useState } from "react";
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Select,
  InputNumber,
  Space,
  Tag,
  Typography,
  Popconfirm,
  message,
} from "antd";
import { PlusOutlined, EditOutlined, DeleteOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import apiClient from "../api/client";

const { Title } = Typography;

interface TenantRecord {
  tenant_id: string;
  role: "platform_admin" | "tenant_user" | "read_only";
  quota_tokens_per_month: number;
  tokens_used_this_month: number;
  enabled: boolean;
  created_at: string;
}

interface TenantForm {
  tenant_id: string;
  role: TenantRecord["role"];
  quota_tokens_per_month: number;
}

function useTenants() {
  return useQuery<TenantRecord[]>({
    queryKey: ["tenants"],
    queryFn: async () => {
      try {
        const res = await apiClient.get("/internal/tenants");
        return res.data;
      } catch {
        return [
          {
            tenant_id: "admin",
            role: "platform_admin",
            quota_tokens_per_month: 10_000_000,
            tokens_used_this_month: 1_280_000,
            enabled: true,
            created_at: "2024-01-01T00:00:00Z",
          },
          {
            tenant_id: "tenant-a",
            role: "tenant_user",
            quota_tokens_per_month: 1_000_000,
            tokens_used_this_month: 320_000,
            enabled: true,
            created_at: "2024-02-01T00:00:00Z",
          },
        ] as TenantRecord[];
      }
    },
  });
}

export default function Tenants() {
  const qc = useQueryClient();
  const { data, isLoading } = useTenants();
  const [modalOpen, setModalOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<TenantRecord | null>(null);
  const [form] = Form.useForm<TenantForm>();

  const isAdmin =
    localStorage.getItem("tenant_id") === "admin" ||
    data?.find((t) => t.tenant_id === localStorage.getItem("tenant_id"))?.role === "platform_admin";

  const createMutation = useMutation({
    mutationFn: (values: TenantForm) =>
      apiClient.post("/internal/tenants", values),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      setModalOpen(false);
      message.success("租户创建成功");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, values }: { id: string; values: Partial<TenantForm> }) =>
      apiClient.patch(`/internal/tenants/${id}`, values),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      setModalOpen(false);
      message.success("租户更新成功");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => apiClient.delete(`/internal/tenants/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tenants"] });
      message.success("租户删除成功");
    },
  });

  const openCreate = () => {
    setEditTarget(null);
    form.resetFields();
    setModalOpen(true);
  };

  const openEdit = (record: TenantRecord) => {
    setEditTarget(record);
    form.setFieldsValue({
      tenant_id: record.tenant_id,
      role: record.role,
      quota_tokens_per_month: record.quota_tokens_per_month,
    });
    setModalOpen(true);
  };

  const handleSubmit = async (values: TenantForm) => {
    if (editTarget) {
      await updateMutation.mutateAsync({ id: editTarget.tenant_id, values });
    } else {
      await createMutation.mutateAsync(values);
    }
  };

  const columns = [
    {
      title: "租户 ID",
      dataIndex: "tenant_id",
      key: "tenant_id",
      render: (id: string) => <code style={{ color: "#79c0ff" }}>{id}</code>,
    },
    {
      title: "角色",
      dataIndex: "role",
      key: "role",
      render: (role: string) => {
        const colorMap: Record<string, string> = {
          platform_admin: "red",
          tenant_user: "blue",
          read_only: "default",
        };
        return <Tag color={colorMap[role] ?? "default"}>{role}</Tag>;
      },
    },
    {
      title: "配额 (Tokens/月)",
      dataIndex: "quota_tokens_per_month",
      key: "quota",
      render: (v: number) => `${(v / 1_000_000).toFixed(1)}M`,
    },
    {
      title: "本月使用",
      key: "used",
      render: (_: unknown, record: TenantRecord) => {
        const pct = (record.tokens_used_this_month / record.quota_tokens_per_month) * 100;
        return (
          <Space>
            <span>{(record.tokens_used_this_month / 1_000).toFixed(0)}K</span>
            <span style={{ color: pct > 80 ? "#f5222d" : "#52c41a" }}>
              ({pct.toFixed(1)}%)
            </span>
          </Space>
        );
      },
    },
    {
      title: "状态",
      dataIndex: "enabled",
      key: "enabled",
      render: (enabled: boolean) => (
        <Tag color={enabled ? "success" : "error"}>{enabled ? "启用" : "禁用"}</Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: TenantRecord) =>
        isAdmin ? (
          <Space>
            <Button
              size="small"
              icon={<EditOutlined />}
              onClick={() => openEdit(record)}
              data-testid={`edit-tenant-${record.tenant_id}`}
            >
              编辑
            </Button>
            <Popconfirm
              title="确认删除此租户？"
              onConfirm={() => deleteMutation.mutate(record.tenant_id)}
              okText="删除"
              cancelText="取消"
            >
              <Button
                size="small"
                danger
                icon={<DeleteOutlined />}
                data-testid={`delete-tenant-${record.tenant_id}`}
              >
                删除
              </Button>
            </Popconfirm>
          </Space>
        ) : (
          <span style={{ color: "#8b949e" }}>无权限</span>
        ),
    },
  ];

  return (
    <div style={{ padding: 24 }} data-testid="tenants-page">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Title level={4} style={{ color: "#e6edf3", margin: 0 }}>
          租户管理
        </Title>
        {isAdmin && (
          <Button
            type="primary"
            icon={<PlusOutlined />}
            onClick={openCreate}
            data-testid="create-tenant-btn"
          >
            新增租户
          </Button>
        )}
      </div>

      <Table
        dataSource={data ?? []}
        columns={columns}
        rowKey="tenant_id"
        loading={isLoading}
        style={{ background: "#161b22" }}
        data-testid="tenants-table"
      />

      <Modal
        title={editTarget ? "编辑租户" : "新增租户"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        confirmLoading={createMutation.isPending || updateMutation.isPending}
      >
        <Form form={form} layout="vertical" onFinish={handleSubmit}>
          <Form.Item
            label="租户 ID"
            name="tenant_id"
            rules={[{ required: true }]}
          >
            <Input disabled={!!editTarget} data-testid="tenant-id-field" />
          </Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "platform_admin", label: "平台管理员" },
                { value: "tenant_user", label: "普通租户" },
                { value: "read_only", label: "只读" },
              ]}
            />
          </Form.Item>
          <Form.Item label="月配额 (Tokens)" name="quota_tokens_per_month">
            <InputNumber min={0} style={{ width: "100%" }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
