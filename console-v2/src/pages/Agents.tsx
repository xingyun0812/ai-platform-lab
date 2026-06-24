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
  Card,
  Collapse,
} from "antd";
import {
  PlusOutlined,
  EditOutlined,
  DeleteOutlined,
  HistoryOutlined,
  SendOutlined,
  PartitionOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
} from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  agentApi,
  AgentSpec,
  DelegateRequest,
  AgentPlan,
  AgentRunResponse,
  BlackboardResponse,
} from "../api/agent";

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

  const defaultGoal =
    "数据分析：搜索 AI analytics 背景，对 demo_sales SQL 区域聚合，再 calc 算同比";
  const [plannerGoal, setPlannerGoal] = useState(defaultGoal);
  const [plannerSession, setPlannerSession] = useState("console-plan-demo");
  const [planPreview, setPlanPreview] = useState<AgentPlan | null>(null);
  const [runResult, setRunResult] = useState<AgentRunResponse | null>(null);
  const tenantId = localStorage.getItem("tenant_id") || "admin";

  const planMutation = useMutation({
    mutationFn: () => agentApi.createPlan(tenantId, plannerGoal),
    onSuccess: (data) => {
      setPlanPreview(data.plan);
      message.success("Plan 已生成");
    },
  });

  const autoPlanMutation = useMutation({
    mutationFn: () => agentApi.runAutoPlan(tenantId, plannerSession, plannerGoal),
    onSuccess: (data) => {
      setRunResult(data);
      if (data.plan) setPlanPreview(data.plan);
      message.success(`执行完成：${data.status}`);
      qc.invalidateQueries({ queryKey: ["blackboard", plannerSession] });
    },
  });

  const { data: blackboard, refetch: refetchBlackboard } = useQuery<BlackboardResponse>({
    queryKey: ["blackboard", plannerSession],
    queryFn: () => agentApi.getBlackboard(plannerSession),
    enabled: false,
  });

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

      <Card
        title={
          <Space>
            <PartitionOutlined />
            <span>Task Planner 演示（O1）</span>
          </Space>
        }
        style={{ marginBottom: 24, background: "#161b22", borderColor: "#30363d" }}
        data-testid="planner-demo-card"
      >
        <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
          生成结构化 Plan 或 <code>auto_plan</code> 逐步执行；会话黑板与 Multi-Agent 共享。
        </Text>
        <Space direction="vertical" style={{ width: "100%" }} size="middle">
          <Input
            addonBefore="session_id"
            value={plannerSession}
            onChange={(e) => setPlannerSession(e.target.value)}
            data-testid="planner-session-input"
          />
          <TextArea
            rows={2}
            value={plannerGoal}
            onChange={(e) => setPlannerGoal(e.target.value)}
            placeholder="任务目标 goal…"
            data-testid="planner-goal-input"
          />
          <Space wrap>
            <Button
              icon={<PartitionOutlined />}
              loading={planMutation.isPending}
              onClick={() => planMutation.mutate()}
              data-testid="planner-generate-btn"
            >
              仅生成 Plan
            </Button>
            <Button
              type="primary"
              icon={<PlayCircleOutlined />}
              loading={autoPlanMutation.isPending}
              onClick={() => autoPlanMutation.mutate()}
              data-testid="planner-autoplan-btn"
            >
              auto_plan 执行
            </Button>
            <Button
              icon={<ReloadOutlined />}
              onClick={() => refetchBlackboard()}
              data-testid="planner-blackboard-btn"
            >
              刷新黑板
            </Button>
          </Space>
          {planPreview && planPreview.steps?.length > 0 && (
            <Table
              size="small"
              pagination={false}
              rowKey="id"
              data-testid="planner-steps-table"
              dataSource={planPreview.steps}
              columns={[
                { title: "Step", dataIndex: "id", width: 72 },
                { title: "描述", dataIndex: "description" },
                {
                  title: "工具",
                  dataIndex: "tool_hint",
                  width: 120,
                  render: (v: string | null) => (v ? <Tag>{v}</Tag> : "—"),
                },
                {
                  title: "依赖",
                  dataIndex: "depends_on",
                  render: (deps: string[]) => (deps?.length ? deps.join(", ") : "—"),
                },
              ]}
            />
          )}
          <Collapse
            ghost
            items={[
              {
                key: "run",
                label: "执行结果 JSON",
                children: (
                  <pre
                    style={{
                      background: "#0d1117",
                      padding: 12,
                      borderRadius: 6,
                      fontSize: 11,
                      maxHeight: 240,
                      overflow: "auto",
                      color: "#e6edf3",
                    }}
                  >
                    {runResult
                      ? JSON.stringify(runResult, null, 2)
                      : "（尚未执行 auto_plan）"}
                  </pre>
                ),
              },
              {
                key: "bb",
                label: `黑板 (${blackboard?.count ?? 0} 条)`,
                children: (
                  <pre
                    style={{
                      background: "#0d1117",
                      padding: 12,
                      borderRadius: 6,
                      fontSize: 11,
                      maxHeight: 200,
                      overflow: "auto",
                      color: "#e6edf3",
                    }}
                  >
                    {blackboard
                      ? JSON.stringify(blackboard.entries, null, 2)
                      : "（点击刷新黑板）"}
                  </pre>
                ),
              },
            ]}
          />
        </Space>
      </Card>

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
