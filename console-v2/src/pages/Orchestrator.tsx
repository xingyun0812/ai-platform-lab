import React, { useState } from "react";
import {
  Table,
  Button,
  Modal,
  Form,
  Input,
  Switch,
  Space,
  Tag,
  Typography,
  Popconfirm,
  message,
  Alert,
  Divider,
  Spin,
} from "antd";
import {
  PlusOutlined,
  PlayCircleOutlined,
  EditOutlined,
  DeleteOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { orchestratorApi, WorkflowDefinition, ExecuteWorkflowResult } from "../api/orchestrator";

const { Title, Text } = Typography;
const { TextArea } = Input;

const WORKFLOW_TEMPLATE = JSON.stringify(
  {
    nodes: [
      { node_id: "start", node_type: "start" },
      {
        node_id: "step1",
        node_type: "llm_call",
        config: { model: "gpt-4o-mini", prompt: "Hello, ${input.message}" },
      },
      { node_id: "end", node_type: "end" },
    ],
    edges: [
      { from_node: "start", to_node: "step1" },
      { from_node: "step1", to_node: "end" },
    ],
  },
  null,
  2
);

export default function Orchestrator() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery<WorkflowDefinition[]>({
    queryKey: ["workflows"],
    queryFn: orchestratorApi.list,
  });

  const [modalOpen, setModalOpen] = useState(false);
  const [editTarget, setEditTarget] = useState<WorkflowDefinition | null>(null);
  const [executeModalOpen, setExecuteModalOpen] = useState(false);
  const [selectedWorkflow, setSelectedWorkflow] = useState<WorkflowDefinition | null>(null);
  const [executeInput, setExecuteInput] = useState('{"message": "Hello!"}');
  const [executeResult, setExecuteResult] = useState<ExecuteWorkflowResult | null>(null);
  const [jsonError, setJsonError] = useState<string | null>(null);
  const [workflowJson, setWorkflowJson] = useState(WORKFLOW_TEMPLATE);
  const [form] = Form.useForm<{ workflow_id: string; name: string; description: string; enabled: boolean }>();

  const createMutation = useMutation({
    mutationFn: orchestratorApi.create,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      setModalOpen(false);
      message.success("工作流创建成功");
    },
  });

  const deleteMutation = useMutation({
    mutationFn: orchestratorApi.delete,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["workflows"] });
      message.success("工作流删除成功");
    },
  });

  const executeMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: Record<string, unknown> }) =>
      orchestratorApi.execute(id, { input }),
    onSuccess: (result) => {
      setExecuteResult(result);
    },
  });

  const handleWorkflowJsonChange = (value: string) => {
    setWorkflowJson(value);
    try {
      JSON.parse(value);
      setJsonError(null);
    } catch (e) {
      setJsonError((e as Error).message);
    }
  };

  const handleCreate = async (values: { workflow_id: string; name: string; description: string; enabled: boolean }) => {
    try {
      const parsed = JSON.parse(workflowJson);
      await createMutation.mutateAsync({ ...values, ...parsed });
    } catch {
      message.error("工作流 JSON 格式错误");
    }
  };

  const handleExecute = () => {
    if (!selectedWorkflow) return;
    try {
      const input = JSON.parse(executeInput);
      executeMutation.mutate({ id: selectedWorkflow.workflow_id, input });
    } catch {
      message.error("输入 JSON 格式错误");
    }
  };

  const columns = [
    {
      title: "工作流 ID",
      dataIndex: "workflow_id",
      key: "workflow_id",
      render: (id: string) => <code style={{ color: "#79c0ff" }}>{id}</code>,
    },
    { title: "名称", dataIndex: "name", key: "name" },
    {
      title: "节点数",
      key: "nodes",
      render: (_: unknown, r: WorkflowDefinition) => r.nodes?.length ?? 0,
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
      render: (_: unknown, record: WorkflowDefinition) => (
        <Space>
          <Button
            size="small"
            type="primary"
            icon={<PlayCircleOutlined />}
            onClick={() => {
              setSelectedWorkflow(record);
              setExecuteResult(null);
              setExecuteModalOpen(true);
            }}
            data-testid={`execute-btn-${record.workflow_id}`}
          >
            执行
          </Button>
          <Button
            size="small"
            icon={<EditOutlined />}
            onClick={() => {
              setEditTarget(record);
              form.setFieldsValue(record);
              setWorkflowJson(JSON.stringify({ nodes: record.nodes, edges: record.edges }, null, 2));
              setModalOpen(true);
            }}
          />
          <Popconfirm
            title="确认删除此工作流？"
            onConfirm={() => deleteMutation.mutate(record.workflow_id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }} data-testid="orchestrator-page">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Title level={4} style={{ color: "#e6edf3", margin: 0 }}>
          工作流编排
        </Title>
        <Button
          type="primary"
          icon={<PlusOutlined />}
          onClick={() => {
            setEditTarget(null);
            form.resetFields();
            setWorkflowJson(WORKFLOW_TEMPLATE);
            setModalOpen(true);
          }}
          data-testid="create-workflow-btn"
        >
          新建工作流
        </Button>
      </div>

      <Table
        dataSource={data ?? []}
        columns={columns}
        rowKey="workflow_id"
        loading={isLoading}
        data-testid="workflows-table"
      />

      {/* Create/Edit Modal */}
      <Modal
        title={editTarget ? "编辑工作流" : "新建工作流"}
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => form.submit()}
        width={700}
        confirmLoading={createMutation.isPending}
      >
        <Form form={form} layout="vertical" onFinish={handleCreate}>
          <Form.Item label="工作流 ID" name="workflow_id" rules={[{ required: !editTarget }]}>
            <Input disabled={!!editTarget} />
          </Form.Item>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input />
          </Form.Item>
          <Form.Item label="启用" name="enabled" valuePropName="checked" initialValue={true}>
            <Switch />
          </Form.Item>
        </Form>

        <Divider>工作流定义 (JSON)</Divider>
        <TextArea
          value={workflowJson}
          onChange={(e) => handleWorkflowJsonChange(e.target.value)}
          rows={12}
          style={{ fontFamily: "monospace", fontSize: 12 }}
          data-testid="workflow-json-editor"
        />
        {jsonError && <Alert message={jsonError} type="error" style={{ marginTop: 4 }} />}
      </Modal>

      {/* Execute Modal */}
      <Modal
        title={`执行工作流: ${selectedWorkflow?.name ?? ""}`}
        open={executeModalOpen}
        onCancel={() => setExecuteModalOpen(false)}
        onOk={handleExecute}
        confirmLoading={executeMutation.isPending}
        width={600}
      >
        <Text style={{ color: "#8b949e" }}>输入 (JSON)：</Text>
        <TextArea
          value={executeInput}
          onChange={(e) => setExecuteInput(e.target.value)}
          rows={4}
          style={{ fontFamily: "monospace", fontSize: 12, marginTop: 8 }}
          data-testid="execute-input"
        />

        {executeMutation.isPending && (
          <div style={{ textAlign: "center", padding: 20 }}>
            <Spin /> <span style={{ marginLeft: 8, color: "#8b949e" }}>执行中...</span>
          </div>
        )}

        {executeResult && (
          <>
            <Divider />
            <Space>
              {executeResult.status === "completed" ? (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              ) : (
                <CloseCircleOutlined style={{ color: "#f5222d" }} />
              )}
              <Tag color={executeResult.status === "completed" ? "success" : "error"}>
                {executeResult.status}
              </Tag>
              <Text style={{ color: "#8b949e" }}>
                {executeResult.total_latency_ms}ms
              </Text>
            </Space>
            <pre
              style={{
                background: "#0d1117",
                padding: 12,
                borderRadius: 6,
                marginTop: 8,
                fontSize: 12,
                maxHeight: 300,
                overflow: "auto",
                color: "#e6edf3",
              }}
            >
              {JSON.stringify(executeResult, null, 2)}
            </pre>
          </>
        )}
      </Modal>
    </div>
  );
}
