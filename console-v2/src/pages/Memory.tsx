import React, { useState } from "react";
import {
  Table,
  Button,
  Input,
  Space,
  Tag,
  Typography,
  Modal,
  Descriptions,
  Select,
  Popconfirm,
  message,
  Row,
  Col,
} from "antd";
import { SearchOutlined, DeleteOutlined, EyeOutlined } from "@ant-design/icons";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { memoryApi, MemoryEntry, MemorySearchRequest } from "../api/memory";

const { Title, Text } = Typography;

export default function Memory() {
  const qc = useQueryClient();
  const [searchParams, setSearchParams] = useState<MemorySearchRequest>({ limit: 20, offset: 0 });
  const [detailOpen, setDetailOpen] = useState(false);
  const [selectedMemory, setSelectedMemory] = useState<MemoryEntry | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["memories", searchParams],
    queryFn: () => memoryApi.search(searchParams),
  });

  const deleteMutation = useMutation({
    mutationFn: memoryApi.delete,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["memories"] });
      message.success("记忆删除成功");
    },
  });

  const handleSearch = () => {
    qc.invalidateQueries({ queryKey: ["memories"] });
  };

  const openDetail = (record: MemoryEntry) => {
    setSelectedMemory(record);
    setDetailOpen(true);
  };

  const columns = [
    {
      title: "记忆 ID",
      dataIndex: "memory_id",
      key: "memory_id",
      width: 200,
      render: (id: string) => (
        <Text style={{ color: "#79c0ff", fontSize: 11 }} copyable>
          {id}
        </Text>
      ),
    },
    {
      title: "内容",
      dataIndex: "content",
      key: "content",
      render: (content: string) => (
        <Text style={{ color: "#c9d1d9" }} ellipsis={{ tooltip: content }}>
          {content}
        </Text>
      ),
    },
    {
      title: "类型",
      dataIndex: "memory_type",
      key: "memory_type",
      width: 120,
      render: (t: string) => <Tag color="blue">{t || "general"}</Tag>,
    },
    {
      title: "会话 ID",
      dataIndex: "session_id",
      key: "session_id",
      width: 150,
      render: (id: string) =>
        id ? <code style={{ fontSize: 11, color: "#8b949e" }}>{id}</code> : "-",
    },
    {
      title: "标签",
      dataIndex: "tags",
      key: "tags",
      width: 150,
      render: (tags: string[]) =>
        tags?.length ? tags.map((t) => <Tag key={t}>{t}</Tag>) : null,
    },
    {
      title: "访问次数",
      dataIndex: "access_count",
      key: "access_count",
      width: 80,
    },
    {
      title: "创建时间",
      dataIndex: "created_at",
      key: "created_at",
      width: 160,
      render: (t: string) => new Date(t).toLocaleString("zh-CN"),
    },
    {
      title: "操作",
      key: "actions",
      width: 100,
      render: (_: unknown, record: MemoryEntry) => (
        <Space>
          <Button size="small" icon={<EyeOutlined />} onClick={() => openDetail(record)} />
          <Popconfirm
            title="确认删除此记忆？"
            onConfirm={() => deleteMutation.mutate(record.memory_id)}
          >
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }} data-testid="memory-page">
      <Title level={4} style={{ color: "#e6edf3", marginBottom: 16 }}>
        记忆管理
      </Title>

      {/* Search filters */}
      <Row gutter={[8, 8]} style={{ marginBottom: 16 }}>
        <Col flex="auto">
          <Input
            placeholder="搜索记忆内容..."
            prefix={<SearchOutlined />}
            onChange={(e) =>
              setSearchParams((prev) => ({ ...prev, query: e.target.value }))
            }
            allowClear
            data-testid="memory-search-input"
          />
        </Col>
        <Col>
          <Select
            placeholder="记忆类型"
            allowClear
            style={{ width: 140 }}
            onChange={(v) =>
              setSearchParams((prev) => ({ ...prev, memory_type: v }))
            }
            options={[
              { value: "general", label: "通用" },
              { value: "preference", label: "偏好" },
              { value: "fact", label: "事实" },
              { value: "episodic", label: "情景" },
            ]}
            data-testid="memory-type-filter"
          />
        </Col>
        <Col>
          <Input
            placeholder="会话 ID"
            style={{ width: 160 }}
            onChange={(e) =>
              setSearchParams((prev) => ({ ...prev, session_id: e.target.value }))
            }
            allowClear
          />
        </Col>
        <Col>
          <Button type="primary" icon={<SearchOutlined />} onClick={handleSearch} data-testid="memory-search-btn">
            搜索
          </Button>
        </Col>
      </Row>

      <Table
        dataSource={data?.memories ?? []}
        columns={columns}
        rowKey="memory_id"
        loading={isLoading}
        pagination={{
          total: data?.total ?? 0,
          pageSize: searchParams.limit ?? 20,
          onChange: (page) =>
            setSearchParams((prev) => ({
              ...prev,
              offset: ((page - 1) * (prev.limit ?? 20)),
            })),
        }}
        scroll={{ x: 900 }}
        data-testid="memories-table"
      />

      {/* Detail Modal */}
      <Modal
        title="记忆详情"
        open={detailOpen}
        onCancel={() => setDetailOpen(false)}
        footer={null}
        width={600}
      >
        {selectedMemory && (
          <Descriptions column={1} bordered size="small">
            <Descriptions.Item label="记忆 ID">{selectedMemory.memory_id}</Descriptions.Item>
            <Descriptions.Item label="内容">{selectedMemory.content}</Descriptions.Item>
            <Descriptions.Item label="类型">{selectedMemory.memory_type}</Descriptions.Item>
            <Descriptions.Item label="会话 ID">{selectedMemory.session_id ?? "-"}</Descriptions.Item>
            <Descriptions.Item label="标签">
              {selectedMemory.tags?.join(", ") || "-"}
            </Descriptions.Item>
            <Descriptions.Item label="访问次数">{selectedMemory.access_count}</Descriptions.Item>
            <Descriptions.Item label="创建时间">{selectedMemory.created_at}</Descriptions.Item>
            <Descriptions.Item label="最后访问">{selectedMemory.accessed_at}</Descriptions.Item>
            {selectedMemory.metadata && (
              <Descriptions.Item label="元数据">
                <pre style={{ margin: 0, fontSize: 12 }}>
                  {JSON.stringify(selectedMemory.metadata, null, 2)}
                </pre>
              </Descriptions.Item>
            )}
          </Descriptions>
        )}
      </Modal>
    </div>
  );
}
