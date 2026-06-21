import React, { useState } from "react";
import {
  Table,
  Select,
  Space,
  Tag,
  Typography,
  DatePicker,
  Button,
  Drawer,
  Descriptions,
  Divider,
  Card,
  Row,
  Col,
  Form,
  Input,
  message,
} from "antd";
import { SearchOutlined, EyeOutlined, ToolOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { auditApi, AuditRecord, AuditFilter, ActionLevel, ToolClassification } from "../api/audit";
import type { Dayjs } from "dayjs";

const { Title, Text } = Typography;
const { RangePicker } = DatePicker;

const ACTION_LEVEL_COLORS: Record<ActionLevel, string> = {
  read_only: "blue",
  write: "orange",
  destructive: "red",
};

const OUTCOME_COLORS: Record<string, string> = {
  success: "success",
  failure: "error",
  blocked: "warning",
};

export default function Audit() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<AuditFilter>({ limit: 20, offset: 0 });
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedRecord, setSelectedRecord] = useState<AuditRecord | null>(null);
  const [toolMgmtOpen, setToolMgmtOpen] = useState(false);
  const [tcForm] = Form.useForm<ToolClassification>();

  const { data, isLoading } = useQuery({
    queryKey: ["audit-logs", filter],
    queryFn: () => auditApi.list(filter),
  });

  const { data: toolClassifications } = useQuery({
    queryKey: ["tool-classifications"],
    queryFn: auditApi.listToolClassifications,
    enabled: toolMgmtOpen,
  });

  const upsertTcMutation = useMutation({
    mutationFn: auditApi.upsertToolClassification,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["tool-classifications"] });
      tcForm.resetFields();
      message.success("工具分类已保存");
    },
  });

  const handleDateRange = (dates: [Dayjs | null, Dayjs | null] | null) => {
    if (!dates) {
      setFilter((prev) => ({ ...prev, start_time: undefined, end_time: undefined }));
      return;
    }
    setFilter((prev) => ({
      ...prev,
      start_time: dates[0]?.toISOString(),
      end_time: dates[1]?.toISOString(),
    }));
  };

  const columns = [
    {
      title: "时间",
      dataIndex: "timestamp",
      key: "timestamp",
      width: 160,
      render: (t: string) => new Date(t).toLocaleString("zh-CN"),
    },
    {
      title: "操作",
      dataIndex: "action",
      key: "action",
      render: (action: string) => <code style={{ color: "#c9d1d9" }}>{action}</code>,
    },
    {
      title: "级别",
      dataIndex: "action_level",
      key: "action_level",
      width: 110,
      render: (level: ActionLevel) => (
        <Tag color={ACTION_LEVEL_COLORS[level] ?? "default"}>{level}</Tag>
      ),
    },
    {
      title: "资源类型",
      dataIndex: "resource_type",
      key: "resource_type",
      width: 120,
    },
    {
      title: "租户",
      dataIndex: "tenant_id",
      key: "tenant_id",
      width: 120,
      render: (id: string) => <code style={{ fontSize: 11 }}>{id}</code>,
    },
    {
      title: "结果",
      dataIndex: "outcome",
      key: "outcome",
      width: 80,
      render: (outcome: string) => (
        <Tag color={OUTCOME_COLORS[outcome] ?? "default"}>{outcome}</Tag>
      ),
    },
    {
      title: "操作",
      key: "actions",
      width: 60,
      render: (_: unknown, record: AuditRecord) => (
        <Button
          size="small"
          icon={<EyeOutlined />}
          onClick={() => {
            setSelectedRecord(record);
            setDetailOpen(true);
          }}
        />
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }} data-testid="audit-page">
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
        <Title level={4} style={{ color: "#e6edf3", margin: 0 }}>
          审计日志
        </Title>
        <Button
          icon={<ToolOutlined />}
          onClick={() => setToolMgmtOpen(true)}
          data-testid="tool-classifications-btn"
        >
          工具分类管理
        </Button>
      </div>

      {/* Filters */}
      <Row gutter={[8, 8]} style={{ marginBottom: 16 }}>
        <Col>
          <Select
            placeholder="操作级别"
            allowClear
            style={{ width: 140 }}
            onChange={(v) => setFilter((prev) => ({ ...prev, action_level: v }))}
            options={[
              { value: "read_only", label: "只读" },
              { value: "write", label: "写入" },
              { value: "destructive", label: "破坏性" },
            ]}
            data-testid="action-level-filter"
          />
        </Col>
        <Col>
          <Select
            placeholder="执行结果"
            allowClear
            style={{ width: 120 }}
            onChange={(v) => setFilter((prev) => ({ ...prev, outcome: v }))}
            options={[
              { value: "success", label: "成功" },
              { value: "failure", label: "失败" },
              { value: "blocked", label: "拦截" },
            ]}
            data-testid="outcome-filter"
          />
        </Col>
        <Col>
          <Input
            placeholder="租户 ID"
            style={{ width: 150 }}
            onChange={(e) => setFilter((prev) => ({ ...prev, tenant_id: e.target.value }))}
            allowClear
          />
        </Col>
        <Col>
          <RangePicker
            showTime
            onChange={(dates) =>
              handleDateRange(dates as [Dayjs | null, Dayjs | null] | null)
            }
          />
        </Col>
        <Col>
          <Button
            type="primary"
            icon={<SearchOutlined />}
            onClick={() => qc.invalidateQueries({ queryKey: ["audit-logs"] })}
          >
            刷新
          </Button>
        </Col>
      </Row>

      <Table
        dataSource={data?.records ?? []}
        columns={columns}
        rowKey="record_id"
        loading={isLoading}
        pagination={{
          total: data?.total ?? 0,
          pageSize: filter.limit ?? 20,
          onChange: (page) =>
            setFilter((prev) => ({ ...prev, offset: (page - 1) * (prev.limit ?? 20) })),
        }}
        scroll={{ x: 800 }}
        data-testid="audit-table"
      />

      {/* Detail Drawer */}
      <Drawer
        title="审计记录详情"
        open={detailOpen}
        onClose={() => setDetailOpen(false)}
        width={480}
      >
        {selectedRecord && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="记录 ID">{selectedRecord.record_id}</Descriptions.Item>
            <Descriptions.Item label="操作">{selectedRecord.action}</Descriptions.Item>
            <Descriptions.Item label="操作级别">
              <Tag color={ACTION_LEVEL_COLORS[selectedRecord.action_level]}>
                {selectedRecord.action_level}
              </Tag>
            </Descriptions.Item>
            <Descriptions.Item label="租户">{selectedRecord.tenant_id}</Descriptions.Item>
            <Descriptions.Item label="资源类型">{selectedRecord.resource_type}</Descriptions.Item>
            <Descriptions.Item label="资源 ID">{selectedRecord.resource_id ?? "-"}</Descriptions.Item>
            <Descriptions.Item label="结果">
              <Tag color={OUTCOME_COLORS[selectedRecord.outcome]}>{selectedRecord.outcome}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="IP">{selectedRecord.actor_ip ?? "-"}</Descriptions.Item>
            <Descriptions.Item label="时间">{selectedRecord.timestamp}</Descriptions.Item>
            {selectedRecord.details && (
              <Descriptions.Item label="详情">
                <pre style={{ margin: 0, fontSize: 11 }}>
                  {JSON.stringify(selectedRecord.details, null, 2)}
                </pre>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Drawer>

      {/* Tool Classification Management Drawer */}
      <Drawer
        title="工具分类管理"
        open={toolMgmtOpen}
        onClose={() => setToolMgmtOpen(false)}
        width={480}
        data-testid="tool-mgmt-drawer"
      >
        <Form
          form={tcForm}
          layout="vertical"
          onFinish={(v) => upsertTcMutation.mutate(v)}
        >
          <Form.Item label="工具名称" name="tool_name" rules={[{ required: true }]}>
            <Input placeholder="例如: delete_document" />
          </Form.Item>
          <Form.Item label="操作级别" name="action_level" rules={[{ required: true }]}>
            <Select
              options={[
                { value: "read_only", label: "只读" },
                { value: "write", label: "写入" },
                { value: "destructive", label: "破坏性" },
              ]}
            />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={upsertTcMutation.isPending}>
            保存
          </Button>
        </Form>

        <Divider />
        <Text style={{ color: "#8b949e" }}>已配置工具：</Text>
        {toolClassifications?.map((tc) => (
          <Card
            key={tc.tool_name}
            size="small"
            style={{ marginTop: 8, background: "#0d1117", border: "1px solid #21262d" }}
          >
            <Space>
              <code style={{ color: "#79c0ff" }}>{tc.tool_name}</code>
              <Tag color={ACTION_LEVEL_COLORS[tc.action_level]}>{tc.action_level}</Tag>
            </Space>
          </Card>
        ))}
      </Drawer>
    </div>
  );
}
