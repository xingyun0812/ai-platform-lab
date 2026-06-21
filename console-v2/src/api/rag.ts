import apiClient from "./client";

export interface KnowledgeBase {
  kb_id: string;
  name: string;
  description?: string;
  document_count: number;
  created_at: string;
}

export interface Document {
  doc_id: string;
  kb_id: string;
  filename: string;
  status: "pending" | "processing" | "ready" | "error";
  chunk_count: number;
  created_at: string;
}

export interface QueryResult {
  chunks: Array<{
    chunk_id: string;
    text: string;
    score: number;
    doc_id: string;
    metadata?: Record<string, unknown>;
  }>;
  latency_ms: number;
}

export interface UploadResponse {
  doc_id: string;
  status: string;
  message: string;
}

export const ragApi = {
  listKnowledgeBases: () =>
    apiClient.get<KnowledgeBase[]>("/internal/rag/knowledge-bases").then((r) => r.data),

  createKnowledgeBase: (data: { name: string; description?: string }) =>
    apiClient.post<KnowledgeBase>("/internal/rag/knowledge-bases", data).then((r) => r.data),

  deleteKnowledgeBase: (kbId: string) =>
    apiClient.delete(`/internal/rag/knowledge-bases/${kbId}`),

  listDocuments: (kbId: string) =>
    apiClient.get<Document[]>(`/internal/rag/knowledge-bases/${kbId}/documents`).then((r) => r.data),

  uploadDocument: (kbId: string, file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    return apiClient.post<UploadResponse>(
      `/internal/rag/knowledge-bases/${kbId}/documents`,
      formData,
      { headers: { "Content-Type": "multipart/form-data" } }
    ).then((r) => r.data);
  },

  deleteDocument: (kbId: string, docId: string) =>
    apiClient.delete(`/internal/rag/knowledge-bases/${kbId}/documents/${docId}`),

  query: (text: string, kbId: string, topK = 5) =>
    apiClient
      .post<QueryResult>("/internal/rag/query", { query: text, kb_id: kbId, top_k: topK })
      .then((r) => r.data),
};
