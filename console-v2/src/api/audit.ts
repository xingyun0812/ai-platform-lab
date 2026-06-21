import apiClient from "./client";

export type ActionLevel = "read_only" | "write" | "destructive";

export interface AuditRecord {
  record_id: string;
  tenant_id: string;
  action: string;
  action_level: ActionLevel;
  resource_type: string;
  resource_id?: string;
  outcome: "success" | "failure" | "blocked";
  actor_ip?: string;
  user_agent?: string;
  details?: Record<string, unknown>;
  timestamp: string;
}

export interface AuditFilter {
  action_level?: ActionLevel;
  tenant_id?: string;
  resource_type?: string;
  outcome?: string;
  start_time?: string;
  end_time?: string;
  limit?: number;
  offset?: number;
}

export interface AuditListResponse {
  records: AuditRecord[];
  total: number;
  has_more: boolean;
}

export interface ToolClassification {
  tool_name: string;
  action_level: ActionLevel;
  description?: string;
}

export const auditApi = {
  list: (filter?: AuditFilter) =>
    apiClient.get<AuditListResponse>("/internal/audit", { params: filter }).then((r) => r.data),

  get: (recordId: string) =>
    apiClient.get<AuditRecord>(`/internal/audit/${recordId}`).then((r) => r.data),

  listToolClassifications: () =>
    apiClient.get<ToolClassification[]>("/internal/audit/tools").then((r) => r.data),

  upsertToolClassification: (tc: ToolClassification) =>
    apiClient.put<ToolClassification>("/internal/audit/tools", tc).then((r) => r.data),
};
