import React, { useState } from "react";
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Select,
  Switch,
  Space,
  Tag,
  Typography,
  Drawer,
  Divider,
  InputNumber,
  Popconfirm,
  message,
  Alert,
} from "antd";
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  HistoryOutlined,
  SendOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { agentApi, AgentSpec, DelegateRequest } from "../api/agent";

const { Title, Text } = Typography;
const { TextArea } = Input;

export default function Agents() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<AgentSpec[]>({
    queryKey: ["agents"],
    queryFn: agentApi.list,
  });

  const [modalOpen, setModalOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<AgentSpec | null>(null);
  const [versionDrawerOpen, setVersionDrawerOpen] = useState(false);
  const [delegateModalOpen, setDelegateModalOpen] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<AgentSpec | null>(null);
  const [delegateResult, setDelegateResult] = useState<string | null>(null);
  const [form] = Form.useForm<AgentSpec>();
  const [delegateForm] = Form.useForm<DelegateRequest>();

  const { data: versions } = useQuery({
    queryKey: ["agent-versions", selectedAgent?.agent_id],
    queryFn: () => agentApi.getVersions(selectedAgent!.agent_id),
    enabled: versionDrawerOpen && !!selectedAgent,
  });

  const createMutation = useMutation({
    mutationFn: agentApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      setModalOpen(false);
      message.success("Agent 创建成功");
    },
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, patch }: { id: string; patch: Partial<AgentSpec> }) =>
      agentApi.update(id, patch),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      setModalOpen(false);
      message.success("Agent 更新成功");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: agentApi.delete,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["agents"] });
      message.success("Agent 删除成功");
    },
  });

  const delegateMutation = useMutation({
    mutationFn: ({ id, req }: { id: string; req: DelegateRequest }) =>
      agentApi.delegate(id, req),
    onSuccess: (result) => {
      setDelegateResult(JSON.stringify(result, null, 2));
    },
  });

  const openDelegate = (agent: AgentSpec) => {
    setSelectedAgent(agent);
    delegateForm.resetFields();
    setDelegateResult(null);
    setDelegateModalOpen(true);
  };

  const openVersions = (agent: AgentSpec) => {
    setSelectedAgent(agent);
    setVersionDrawerOpen(true);
  };

  const columns = [
    {
      title: "Agent ID",
      dataIndex: "agent_id",
      key: "agent_id",
      render: (id: string) => <code style={{ color: "#79c0ff" }}>{id}</code>,
    },
    {
      title: "名称",
      dataIndex: "name",
      key: "name",
    },
    {
      title: "角色",
      dataIndex: "role",
      key: "role",
      render: (role: string) => {
        const colors: Record<string, string> = {
          primary: "blue",
          specialist: "green",
          reviewer: "orange",
          router: "purple",
        };
        return <Tag color={colors[role] ?? "default"}>{role}</Tag>;
      },
    },
    {
      title: "可委托",
      dataIndex: "can_delegate",
      key: "can_delegate",
      render: (v: boolean) => <Tag color={v ? "success" : "default"}>{v ? "是" : "否"}</Tag>,
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
      render: (_: unknown, record: AgentSpec) => (
        <Space>
          <Button
            size="small"
            icon={<SendOutlined />}
            onClick={() => openDelegate(record)}
            data-testid={`delegate-btn-${record.agent_id}`}
          >
            委托
          </Button>
          <Button
            size="small"
            icon={<HistoryOutlined />}
            onClick={() => openVersions(record)}
            data-testid={`versions-btn-${record.agent_id}`}
          >
            历史
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditTarget(record);
              form.setFieldsValue(record);
              setModalOpen(true);
            }}
          />
          <Popconfirm
            title="确认删除此 Agent？"
            onConfirm={() => deleteMutation.mutate(record.agent_id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }} data-testid="agents-page">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Title level={4} style={{ color: "#e6edf3", margin: 0 }}>
          Agent 管理
        </Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditTarget(null);
            form.resetFields();
            setModalOpen(true);
          }}
          data-testid="create-agent-btn"
        >
          新增 Agent
        </Button>
      </div>

      <Table
        dataSource={data ?? []}
        columns={columns}
        rowKey="agent_id"
        loading={isLoading}
        data-testid="agents-table"
      />

      {/* Create/Edit Modal */}
      <Modal
        title={editTarget ? "编辑 Agent" : "新增 Agent"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        width={600}
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={(values) => {
            if (editTarget) {
              updateMutation.mutate({ id: editTarget.agent_id, patch: values });
            } else {
              createMutation.mutate(values);
            }
          }}
        >
          <Form.Item label="Agent ID" name="agent_id" rules={[{ required: true }]}>
            <Input disabled={!!editTarget} />
          </Form.Item>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="角色" name="role" rules={[{ required: true }]}>
            <Select
              options={["primary", "specialist", "reviewer", "router"].map((r) => ({
                value: r,
                label: r,
              }))}
            />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input />
          </Form.Item>
          <Form.Item label="System Prompt" name="system_prompt">
            <TextArea rows={4} />
          </Form.Item>
          <Form.Item label="最大委托深度" name="max_delegation_depth">
            <InputNumber min={1} max={10} />
          </Form.Item>
          <Space>
            <Form.Item label="可委托" name="can_delegate" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="可被委托" name="can_be_delegated_to" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item label="启用" name="enabled" valuePropName="checked">
              <Switch defaultChecked />
            </Form.Item>
          </Space>
        </Form>
      </Modal>

      {/* Delegate Dialog */}
      <Modal
        title={`委托任务给 ${selectedAgent?.name ?? ""}`}
        open={delegateModalOpen}
        onCancel={() => setDelegateModalOpen(false)}
        onOk={() => delegateForm.submit()}
        confirmLoading={delegateMutation.isPending}
        width={600}
        data-testid="delegate-modal"
      >
        <Form
          form={delegateForm}
          layout="vertical"
          onFinish={(values) => {
            if (selectedAgent) {
              delegateMutation.mutate({ id: selectedAgent.agent_id, req: values });
            }
          }}
        >
          <Form.Item label="任务描述" name="task" rules={[{ required: true }]}>
            <TextArea rows={3} placeholder="描述要委托的任务..." />
          </Form.Item>
          <Form.Item label="超时 (秒)" name="timeout" initialValue={30}>
            <InputNumber min={5} max={300} />
          </Form.Item>
        </Form>
        {delegateResult && (
          <>
            <Divider />
            <Text style={{ color: "#8b949e" }}>执行结果：</Text>
            <pre
              style={{
                background: "#0d1117",
                padding: 12,
                borderRadius: 6,
                fontSize: 12,
                maxHeight: 200,
                overflow: "auto",
                color: "#e6edf3",
              }}
            >
              {delegateResult}
            </pre>
          </>
        )}
      </Modal>

      {/* Version History Drawer */}
      <Drawer
        title={`${selectedAgent?.name ?? ""} — 版本历史`}
        open={versionDrawerOpen}
        onClose={() => setVersionDrawerOpen(false)}
        width={480}
        data-testid="versions-drawer"
      >
        {versions && versions.length > 0 ? (
          versions.map((v) => (
            <div key={v.version} style={{ marginBottom: 16 }}>
              <Text strong>v{v.version}</Text>
              <Text style={{ color: "#8b949e", marginLeft: 8 }}>{v.updated_at}</Text>
              <pre
                style={{
                  background: "#0d1117",
                  padding: 8,
                  borderRadius: 4,
                  fontSize: 11,
                  marginTop: 4,
                }}
              >
                {JSON.stringify(v.snapshot, null, 2)}
              </pre>
            </div>
          ))
        ) : (
          <Alert message="暂无版本历史" type="info" />
        )}
      </Drawer>
    </div>
  );
}
