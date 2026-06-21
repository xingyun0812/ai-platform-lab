import React, { useState } from "react";
import {
  Layout,
  List,
  Card,
  Button,
  Modal,
  Form,
  Input,
  Upload,
  Table,
  Tag,
  Space,
  Typography,
  Divider,
  message,
  Empty,
} from "antd";
import {
  PlusOutlined,
  UploadOutlined,
  DeleteOutlined,
  SearchOutlined,
  DatabaseOutlined,
  InboxOutlined,
} from "@ant-design/icons";
import type { UploadProps } from "antd";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { ragApi, KnowledgeBase, Document } from "../api/rag";

const { Title, Text } = Typography;
const { Sider, Content } = Layout;
const { Dragger } = Upload;

export default function RAG() {
  const qc = useQueryClient();
  const [selectedKb, setSelectedKb] = useState<KnowledgeBase | null>(null);
  const [createKbOpen, setCreateKbOpen] = useState(false);
  const [queryText, setQueryText] = useState("");
  const [queryResults, setQueryResults] = useState<unknown[]>([]);
  const [queryLoading, setQueryLoading] = useState(false);
  const [kbForm] = Form.useForm<{ name: string; description: string }>();

  const { data: kbs, isLoading: kbLoading } = useQuery<KnowledgeBase[]>({
    queryKey: ["kbs"],
    queryFn: ragApi.listKnowledgeBases,
  });

  const { data: docs } = useQuery<Document[]>({
    queryKey: ["docs", selectedKb?.kb_id],
    queryFn: () => ragApi.listDocuments(selectedKb!.kb_id),
    enabled: !!selectedKb,
  });

  const createKbMutation = useMutation({
    mutationFn: ragApi.createKnowledgeBase,
    onSuccess: (kb) => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      setSelectedKb(kb);
      setCreateKbOpen(false);
      message.success("知识库创建成功");
    },
  });

  const deleteKbMutation = useMutation({
    mutationFn: ragApi.deleteKnowledgeBase,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["kbs"] });
      setSelectedKb(null);
      message.success("知识库删除成功");
    },
  });

  const deleteDocMutation = useMutation({
    mutationFn: ({ kbId, docId }: { kbId: string; docId: string }) =>
      ragApi.deleteDocument(kbId, docId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docs", selectedKb?.kb_id] });
      message.success("文档删除成功");
    },
  });

  const uploadProps: UploadProps = {
    name: "file",
    multiple: false,
    customRequest: async ({ file, onSuccess, onError }) => {
      if (!selectedKb) {
        onError?.(new Error("请先选择知识库"));
        return;
      }
      try {
        const result = await ragApi.uploadDocument(selectedKb.kb_id, file as File);
        qc.invalidateQueries({ queryKey: ["docs", selectedKb.kb_id] });
        message.success(`文档 ${(file as File).name} 上传成功`);
        onSuccess?.(result);
      } catch (e) {
        onError?.(e as Error);
      }
    },
  };

  const handleQuery = async () => {
    if (!selectedKb || !queryText.trim()) return;
    setQueryLoading(true);
    try {
      const result = await ragApi.query(queryText, selectedKb.kb_id);
      setQueryResults(result.chunks);
    } catch {
      setQueryResults([]);
    } finally {
      setQueryLoading(false);
    }
  };

  const docColumns = [
    { title: "文件名", dataIndex: "filename", key: "filename" },
    {
      title: "状态",
      dataIndex: "status",
      key: "status",
      render: (s: string) => {
        const colors: Record<string, string> = {
          ready: "success",
          processing: "processing",
          pending: "default",
          error: "error",
        };
        return <Tag color={colors[s] ?? "default"}>{s}</Tag>;
      },
    },
    { title: "分片数", dataIndex: "chunk_count", key: "chunk_count" },
    {
      title: "操作",
      key: "actions",
      render: (_: unknown, record: Document) => (
        <Button
          size="small"
          danger
          icon={<DeleteOutlined />}
          onClick={() =>
            deleteDocMutation.mutate({ kbId: selectedKb!.kb_id, docId: record.doc_id })
          }
        >
          删除
        </Button>
      ),
    },
  ];

  return (
    <div style={{ padding: 24 }} data-testid="rag-page">
      <Title level={4} style={{ color: "#e6edf3", marginBottom: 16 }}>
        RAG 知识库
      </Title>

      <Layout style={{ background: "transparent", gap: 16 }}>
        {/* Left: KB list */}
        <Sider
          width={260}
          style={{ background: "#161b22", border: "1px solid #30363d", borderRadius: 8 }}
        >
          <div style={{ padding: 12, borderBottom: "1px solid #30363d", display: "flex", justifyContent: "space-between" }}>
            <Text style={{ color: "#c9d1d9" }}>知识库列表</Text>
            <Button
              size="small"
              type="primary"
              icon={<PlusOutlined />}
              onClick={() => setCreateKbOpen(true)}
              data-testid="create-kb-btn"
            />
          </div>
          <List
            loading={kbLoading}
            dataSource={kbs ?? []}
            renderItem={(kb) => (
              <List.Item
                key={kb.kb_id}
                style={{
                  padding: "10px 12px",
                  cursor: "pointer",
                  background: selectedKb?.kb_id === kb.kb_id ? "#1f2937" : "transparent",
                  borderLeft: selectedKb?.kb_id === kb.kb_id ? "2px solid #1677ff" : "2px solid transparent",
                }}
                onClick={() => setSelectedKb(kb)}
              >
                <Space direction="vertical" size={0} style={{ width: "100%" }}>
                  <div style={{ display: "flex", justifyContent: "space-between" }}>
                    <Text style={{ color: "#e6edf3" }}>{kb.name}</Text>
                    <DatabaseOutlined style={{ color: "#8b949e" }} />
                  </div>
                  <Text style={{ color: "#8b949e", fontSize: 12 }}>
                    {kb.document_count} 文档
                  </Text>
                </Space>
              </List.Item>
            )}
          />
        </Sider>

        {/* Right: KB detail */}
        <Content>
          {selectedKb ? (
            <Space direction="vertical" size={16} style={{ width: "100%" }}>
              <Card
                title={
                  <Space>
                    <span style={{ color: "#c9d1d9" }}>{selectedKb.name}</span>
                    <Tag>{selectedKb.kb_id}</Tag>
                  </Space>
                }
                extra={
                  <Button
                    danger
                    size="small"
                    icon={<DeleteOutlined />}
                    onClick={() => deleteKbMutation.mutate(selectedKb.kb_id)}
                  >
                    删除知识库
                  </Button>
                }
                style={{ background: "#161b22", border: "1px solid #30363d" }}
              >
                {/* Upload */}
                <Dragger {...uploadProps} style={{ marginBottom: 16 }} data-testid="upload-dragger">
                  <p className="ant-upload-drag-icon">
                    <InboxOutlined style={{ color: "#1677ff" }} />
                  </p>
                  <p style={{ color: "#c9d1d9" }}>点击或拖拽文件到此区域上传</p>
                  <p style={{ color: "#8b949e", fontSize: 12 }}>
                    支持 PDF、TXT、MD、DOCX 格式
                  </p>
                </Dragger>

                {/* Documents table */}
                <Table
                  dataSource={docs ?? []}
                  columns={docColumns}
                  rowKey="doc_id"
                  size="small"
                  data-testid="docs-table"
                />
              </Card>

              {/* Query panel */}
              <Card
                title={<span style={{ color: "#c9d1d9" }}>查询测试</span>}
                style={{ background: "#161b22", border: "1px solid #30363d" }}
              >
                <Space.Compact style={{ width: "100%", marginBottom: 12 }}>
                  <Input
                    placeholder="输入查询文本..."
                    value={queryText}
                    onChange={(e) => setQueryText(e.target.value)}
                    onPressEnter={handleQuery}
                    data-testid="query-input"
                  />
                  <Button
                    type="primary"
                    icon={<SearchOutlined />}
                    loading={queryLoading}
                    onClick={handleQuery}
                    data-testid="query-btn"
                  >
                    查询
                  </Button>
                </Space.Compact>

                {queryResults.length > 0 && (
                  <div>
                    {(queryResults as Array<{ chunk_id: string; text: string; score: number }>).map((chunk, i) => (
                      <Card
                        key={chunk.chunk_id ?? i}
                        size="small"
                        style={{ marginBottom: 8, background: "#0d1117", border: "1px solid #21262d" }}
                      >
                        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 4 }}>
                          <Tag color="blue">相关度: {chunk.score?.toFixed(3)}</Tag>
                          <Text style={{ color: "#8b949e", fontSize: 11 }}>{chunk.chunk_id}</Text>
                        </div>
                        <Text style={{ color: "#c9d1d9", fontSize: 13 }}>{chunk.text}</Text>
                      </Card>
                    ))}
                  </div>
                )}
              </Card>
            </Space>
          ) : (
            <div style={{ display: "flex", justifyContent: "center", padding: 80 }}>
              <Empty description="请选择知识库" />
            </div>
          )}
        </Content>
      </Layout>

      {/* Create KB Modal */}
      <Modal
        title="新建知识库"
        open={createKbOpen}
        onCancel={() => setCreateKbOpen(false)}
        onOk={() => kbForm.submit()}
      >
        <Form form={kbForm} layout="vertical" onFinish={(v) => createKbMutation.mutate(v)}>
          <Form.Item label="名称" name="name" rules={[{ required: true }]}>
            <Input placeholder="例如: 产品文档" />
          </Form.Item>
          <Form.Item label="描述" name="description">
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
