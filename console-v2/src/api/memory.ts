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

function mapRecord(raw: Record<string, unknown>): MemoryEntry {
  const metadata = (raw.metadata as Record<string, unknown> | undefined) ?? {};
  const scope = String(raw.scope ?? "tenant");
  const scopeId = String(raw.scope_id ?? "");
  return {
    memory_id: String(raw.memory_id ?? ""),
    tenant_id: String(raw.tenant_id ?? ""),
    session_id: scope === "session" ? scopeId : undefined,
    content: String(raw.content ?? raw.summary ?? ""),
    memory_type: String(metadata.memory_type ?? scope),
    tags: Array.isArray(metadata.tags) ? (metadata.tags as string[]) : [],
    created_at: String(raw.created_at ?? new Date().toISOString()),
    accessed_at: String(metadata.accessed_at ?? raw.created_at ?? new Date().toISOString()),
    access_count: Number(metadata.access_count ?? 0),
    metadata,
  };
}

export const memoryApi = {
  search: async (req: MemorySearchRequest): Promise<MemorySearchResponse> => {
    const tenantId = localStorage.getItem("tenant_id") ?? "admin";
    const scope = req.session_id ? "session" : "tenant";
    const scopeId = req.session_id ?? tenantId;
    const limit = req.limit ?? 20;

    if (req.query?.trim()) {
      const res = await apiClient.post<{ results: Record<string, unknown>[]; count: number }>(
        "/internal/memory/search",
        {
          scope,
          scope_id: scopeId,
          query: req.query,
          top_k: limit,
        }
      );
      const memories = res.data.results.map(mapRecord);
      return { memories, total: res.data.count, has_more: false };
    }

    const res = await apiClient.get<{ records: Record<string, unknown>[]; count: number }>(
      "/internal/memory/list",
      { params: { scope, scope_id: scopeId, limit } }
    );
    const memories = res.data.records.map(mapRecord);
    return { memories, total: res.data.count, has_more: memories.length >= limit };
  },

  get: (memoryId: string) =>
    apiClient.get<MemoryEntry>(`/internal/memory/${memoryId}`).then((r) => mapRecord(r.data as unknown as Record<string, unknown>)),

  delete: (memoryId: string) => apiClient.delete(`/internal/memory/${memoryId}`),
};
