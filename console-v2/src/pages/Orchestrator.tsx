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
  ReloadOutlined,
  RedoOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  orchestratorApi,
  WorkflowDefinition,
  ExecuteWorkflowResult,
  ExecutionCheckpoint,
} from "../api/orchestrator";

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
  const [executeInput, setExecuteInput] = useState('{"topic": "AI analytics demo"}');
  const [executeResult, setExecuteResult] = useState<ExecuteWorkflowResult | null>(null);
  const [lastExecutionId, setLastExecutionId] = useState<string | null>(null);
  const [checkpoint, setCheckpoint] = useState<ExecutionCheckpoint | null>(null);
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
    mutationFn: ({ id, inputs }: { id: string; inputs: Record<string, unknown> }) =>
      orchestratorApi.execute(id, { inputs }),
    onSuccess: (result) => {
      setExecuteResult(result);
      setLastExecutionId(result.execution_id ?? null);
      setCheckpoint(null);
    },
  });

  const checkpointMutation = useMutation({
    mutationFn: (executionId: string) => orchestratorApi.getExecution(executionId),
    onSuccess: (cp) => {
      setCheckpoint(cp);
      message.success(`checkpoint: ${cp.status}`);
    },
  });

  const resumeMutation = useMutation({
    mutationFn: ({ executionId, inputs }: { executionId: string; inputs: Record<string, unknown> }) =>
      orchestratorApi.resumeExecution(executionId, inputs),
    onSuccess: (result) => {
      setExecuteResult(result);
      setLastExecutionId(result.execution_id ?? lastExecutionId);
      if (result.execution_id) {
        checkpointMutation.mutate(result.execution_id);
      }
      message.success(`resume 完成: ${result.status}`);
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
      const inputs = JSON.parse(executeInput);
      executeMutation.mutate({ id: selectedWorkflow.workflow_id, inputs });
    } catch {
      message.error("输入 JSON 格式错误");
    }
  };

  const handleResume = () => {
    if (!lastExecutionId || !selectedWorkflow) return;
    try {
      const inputs = JSON.parse(executeInput);
      resumeMutation.mutate({ executionId: lastExecutionId, inputs });
    } catch {
      message.error("输入 JSON 格式错误");
    }
  };

  const canResume =
    checkpoint != null && ["failed", "paused", "running"].includes(checkpoint.status);

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
              setLastExecutionId(null);
              setCheckpoint(null);
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
            <Space wrap>
              {executeResult.status === "completed" ? (
                <CheckCircleOutlined style={{ color: "#52c41a" }} />
              ) : (
                <CloseCircleOutlined style={{ color: "#f5222d" }} />
              )}
              <Tag color={executeResult.status === "completed" ? "success" : "error"}>
                {executeResult.status}
              </Tag>
              {executeResult.execution_time_ms != null && (
                <Text style={{ color: "#8b949e" }}>{executeResult.execution_time_ms}ms</Text>
              )}
            </Space>
            {lastExecutionId && (
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message="Graph Checkpoint"
                description={
                  <Space direction="vertical" style={{ width: "100%" }}>
                    <Text>
                      <code data-testid="execution-id">{lastExecutionId}</code>
                    </Text>
                    <Space wrap>
                      <Button
                        size="small"
                        icon={<ReloadOutlined />}
                        loading={checkpointMutation.isPending}
                        onClick={() => checkpointMutation.mutate(lastExecutionId)}
                        data-testid="checkpoint-refresh-btn"
                      >
                        查看 checkpoint
                      </Button>
                      {canResume && (
                        <Button
                          size="small"
                          type="primary"
                          icon={<RedoOutlined />}
                          loading={resumeMutation.isPending}
                          onClick={handleResume}
                          data-testid="checkpoint-resume-btn"
                        >
                          Resume 继续
                        </Button>
                      )}
                    </Space>
                    {checkpoint && (
                      <pre
                        style={{
                          background: "#0d1117",
                          padding: 8,
                          borderRadius: 4,
                          fontSize: 11,
                          maxHeight: 120,
                          overflow: "auto",
                          color: "#e6edf3",
                          margin: 0,
                        }}
                        data-testid="checkpoint-json"
                      >
                        {JSON.stringify(
                          {
                            status: checkpoint.status,
                            current_node: checkpoint.current_node,
                            trace_len: checkpoint.trace?.length ?? 0,
                          },
                          null,
                          2
                        )}
                      </pre>
                    )}
                  </Space>
                }
                data-testid="execution-checkpoint-panel"
              />
            )}
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
