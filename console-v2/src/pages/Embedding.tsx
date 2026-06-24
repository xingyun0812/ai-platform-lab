import React, { useMemo, useState } from "react";
import {
  Button,
  Card,
  Col,
  Form,
  Input,
  Row,
  Select,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
  Upload,
  message,
} from "antd";
import { PlusOutlined, ThunderboltOutlined, UploadOutlined } from "@ant-design/icons";
import type { UploadProps } from "antd";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  embeddingApi,
  EmbeddingInputItem,
  EmbeddingModel,
} from "../api/embedding";

const { Title, Text, Paragraph } = Typography;

type DraftItem = EmbeddingInputItem & { key: string; label: string };

function fileToBase64(file: File): Promise<{ mime: string; data: string }> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = String(reader.result ?? "");
      const comma = result.indexOf(",");
      const data = comma >= 0 ? result.slice(comma + 1) : result;
      resolve({ mime: file.type || "image/png", data });
    };
    reader.onerror = () => reject(reader.error ?? new Error("read failed"));
    reader.readAsDataURL(file);
  });
}

function modalityOf(item: EmbeddingInputItem): string {
  return item.type === "text" ? "text" : "image";
}

export default function Embedding() {
  const [modelId, setModelId] = useState<string>("stub-multimodal");
  const [drafts, setDrafts] = useState<DraftItem[]>([]);
  const [textInput, setTextInput] = useState("");
  const [imageUrl, setImageUrl] = useState("");

  const { data: models, isLoading: modelsLoading } = useQuery<EmbeddingModel[]>({
    queryKey: ["embedding-models"],
    queryFn: embeddingApi.listModels,
  });

  const selectedModel = useMemo(
    () => models?.find((m) => m.model_id === modelId),
    [models, modelId]
  );

  const embedMutation = useMutation({
    mutationFn: () =>
      embeddingApi.embed({
        model_id: modelId,
        inputs: drafts.map(({ type, ...rest }) => {
          if (type === "text") return { type, text: (rest as { text: string }).text };
          if (type === "image_url") return { type, url: (rest as { url: string }).url };
          return {
            type,
            mime: (rest as { mime: string }).mime,
            data: (rest as { data: string }).data,
          };
        }),
      }),
    onSuccess: (resp) => {
      message.success(`生成 ${resp.embeddings.length} 条向量（${resp.dimensions} 维）`);
    },
  });

  const addText = () => {
    const text = textInput.trim();
    if (!text) {
      message.warning("请输入文本");
      return;
    }
    setDrafts((prev) => [
      ...prev,
      { key: `t-${Date.now()}`, type: "text", text, label: text.slice(0, 48) },
    ]);
    setTextInput("");
  };

  const addImageUrl = () => {
    const url = imageUrl.trim();
    if (!url) {
      message.warning("请输入图片 URL");
      return;
    }
    setDrafts((prev) => [
      ...prev,
      { key: `u-${Date.now()}`, type: "image_url", url, label: url.slice(0, 48) },
    ]);
    setImageUrl("");
  };

  const uploadProps: UploadProps = {
    accept: "image/*",
    showUploadList: false,
    beforeUpload: async (file) => {
      try {
        const { mime, data } = await fileToBase64(file);
        setDrafts((prev) => [
          ...prev,
          {
            key: `b-${Date.now()}`,
            type: "image_base64",
            mime,
            data,
            label: `${file.name} (${mime})`,
          },
        ]);
        message.success(`已添加图片 ${file.name}`);
      } catch {
        message.error("读取图片失败");
      }
      return false;
    },
  };

  const resultRows =
    embedMutation.data?.embeddings.map((vec, index) => ({
      key: String(index),
      index,
      modality: drafts[index] ? modalityOf(drafts[index]) : "—",
      dimensions: vec.length,
      preview: vec.slice(0, 8).map((v) => v.toFixed(4)).join(", "),
    })) ?? [];

  return (
    <div style={{ padding: 24 }}>
      <Title level={4} style={{ color: "#e6edf3", marginBottom: 4 }}>
        多模态 Embedding
      </Title>
      <Paragraph style={{ color: "#8b949e", marginBottom: 24 }}>
        支持 text / image_url / image_base64，调用{" "}
        <Text code>/internal/embeddings/embed</Text>
      </Paragraph>

      <Row gutter={[16, 16]}>
        <Col xs={24} lg={10}>
          <Card
            title="输入"
            style={{ background: "#161b22", borderColor: "#30363d" }}
            headStyle={{ color: "#e6edf3", borderColor: "#30363d" }}
          >
            <Form layout="vertical">
              <Form.Item label={<span style={{ color: "#c9d1d9" }}>模型</span>}>
                <Select
                  loading={modelsLoading}
                  value={modelId}
                  onChange={setModelId}
                  options={(models ?? []).map((m) => ({
                    value: m.model_id,
                    label: (
                      <Space>
                        <span>{m.model_id}</span>
                        {(m.modalities ?? ["text"]).map((mod) => (
                          <Tag key={mod} color={mod === "image" ? "purple" : "blue"}>
                            {mod}
                          </Tag>
                        ))}
                      </Space>
                    ),
                  }))}
                />
              </Form.Item>
              {selectedModel && (
                <Text style={{ color: "#8b949e", fontSize: 12 }}>
                  {selectedModel.dimensions} 维 · {selectedModel.provider}
                </Text>
              )}
            </Form>

            <Tabs
              style={{ marginTop: 16 }}
              items={[
                {
                  key: "text",
                  label: "文本",
                  children: (
                    <Space.Compact style={{ width: "100%" }}>
                      <Input.TextArea
                        rows={3}
                        value={textInput}
                        onChange={(e) => setTextInput(e.target.value)}
                        placeholder="季度销售趋势图"
                      />
                    </Space.Compact>
                  ),
                },
                {
                  key: "url",
                  label: "图片 URL",
                  children: (
                    <Input
                      value={imageUrl}
                      onChange={(e) => setImageUrl(e.target.value)}
                      placeholder="https://example.com/chart.png"
                    />
                  ),
                },
                {
                  key: "file",
                  label: "上传图片",
                  children: (
                    <Upload {...uploadProps}>
                      <Button icon={<UploadOutlined />}>选择图片</Button>
                    </Upload>
                  ),
                },
              ]}
            />

            <Space style={{ marginTop: 12 }}>
              <Button icon={<PlusOutlined />} onClick={addText}>
                添加文本
              </Button>
              <Button icon={<PlusOutlined />} onClick={addImageUrl}>
                添加 URL
              </Button>
            </Space>

            <Table
              style={{ marginTop: 16 }}
              size="small"
              pagination={false}
              dataSource={drafts}
              locale={{ emptyText: "尚未添加输入项" }}
              columns={[
                {
                  title: "类型",
                  dataIndex: "type",
                  width: 110,
                  render: (t: string) => (
                    <Tag color={t === "text" ? "blue" : "purple"}>{t}</Tag>
                  ),
                },
                { title: "内容", dataIndex: "label", ellipsis: true },
                {
                  title: "",
                  width: 64,
                  render: (_, row) => (
                    <Button
                      type="link"
                      danger
                      size="small"
                      onClick={() => setDrafts((prev) => prev.filter((d) => d.key !== row.key))}
                    >
                      删除
                    </Button>
                  ),
                },
              ]}
            />

            <Button
              type="primary"
              icon={<ThunderboltOutlined />}
              block
              style={{ marginTop: 16 }}
              loading={embedMutation.isPending}
              disabled={drafts.length === 0}
              onClick={() => embedMutation.mutate()}
            >
              生成 Embedding
            </Button>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card
            title="结果"
            style={{ background: "#161b22", borderColor: "#30363d" }}
            headStyle={{ color: "#e6edf3", borderColor: "#30363d" }}
          >
            {embedMutation.data ? (
              <>
                <Space wrap style={{ marginBottom: 12 }}>
                  <Tag color="green">model: {embedMutation.data.model_id}</Tag>
                  <Tag>dim: {embedMutation.data.dimensions}</Tag>
                  <Tag>cached: {embedMutation.data.cached}</Tag>
                </Space>
                <Table
                  size="small"
                  pagination={false}
                  dataSource={resultRows}
                  columns={[
                    { title: "#", dataIndex: "index", width: 48 },
                    {
                      title: "modality",
                      dataIndex: "modality",
                      width: 90,
                      render: (m: string) => (
                        <Tag color={m === "image" ? "purple" : "blue"}>{m}</Tag>
                      ),
                    },
                    { title: "dims", dataIndex: "dimensions", width: 72 },
                    { title: "preview (前 8 维)", dataIndex: "preview" },
                  ]}
                />
              </>
            ) : (
              <Text style={{ color: "#8b949e" }}>添加输入并点击生成后显示向量预览</Text>
            )}
          </Card>
        </Col>
      </Row>
    </div>
  );
}
