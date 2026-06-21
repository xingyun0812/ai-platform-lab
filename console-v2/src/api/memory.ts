import apiClient from "./client";

export interface MemoryEntry {
  memory_id: string;
  tenant_id: string;
  session_id?: string;
  content: string;
  memory_type: string;
  tags: string[];
  created_at: string;
  accessed_at: string;
  access_count: number;
  metadata?: Record<string, unknown>;
}

export interface MemorySearchRequest {
  query?: string;
  session_id?: string;
  memory_type?: string;
  tags?: string[];
  limit?: number;
  offset?: number;
}

export interface MemorySearchResponse {
  memories: MemoryEntry[];
  total: number;
  has_more: boolean;
}

export interface CreateMemoryRequest {
  content: string;
  memory_type?: string;
  session_id?: string;
  tags?: string[];
  metadata?: Record<string, unknown>;
}

export const memoryApi = {
  search: (req: MemorySearchRequest) =>
    apiClient.get<MemorySearchResponse>("/internal/memory", { params: req }).then((r) => r.data),

  get: (memoryId: string) =>
    apiClient.get<MemoryEntry>(`/internal/memory/${memoryId}`).then((r) => r.data),

  create: (req: CreateMemoryRequest) =>
    apiClient.post<MemoryEntry>("/internal/memory", req).then((r) => r.data),

  delete: (memoryId: string) =>
    apiClient.delete(`/internal/memory/${memoryId}`),

  deleteBySession: (sessionId: string) =>
    apiClient.delete(`/internal/memory/session/${sessionId}`),
};
