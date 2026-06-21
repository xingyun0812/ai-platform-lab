import apiClient from "./client";

export interface AgentSpec {
  agent_id: string;
  name: string;
  role: "primary" | "specialist" | "reviewer" | "router";
  description: string;
  system_prompt: string;
  model?: string;
  allowed_tools: string[];
  can_delegate: boolean;
  can_be_delegated_to: boolean;
  max_delegation_depth: number;
  enabled: boolean;
}

export interface AgentVersion {
  version: number;
  snapshot: AgentSpec;
  updated_at: string;
}

export interface DelegateRequest {
  task: string;
  inputs?: Record<string, unknown>;
  timeout?: number;
}

export interface DelegateResult {
  agent_id: string;
  task: string;
  output: string;
  success: boolean;
  error?: string;
  latency_ms: number;
}

export const agentApi = {
  list: () =>
    apiClient.get<AgentSpec[]>("/internal/agents").then((r) => r.data),

  get: (agentId: string) =>
    apiClient.get<AgentSpec>(`/internal/agents/${agentId}`).then((r) => r.data),

  create: (spec: Omit<AgentSpec, "agent_id"> & { agent_id?: string }) =>
    apiClient.post<AgentSpec>("/internal/agents", spec).then((r) => r.data),

  update: (agentId: string, patch: Partial<AgentSpec>) =>
    apiClient.patch<AgentSpec>(`/internal/agents/${agentId}`, patch).then((r) => r.data),

  delete: (agentId: string) =>
    apiClient.delete(`/internal/agents/${agentId}`),

  delegate: (agentId: string, req: DelegateRequest) =>
    apiClient
      .post<DelegateResult>(`/internal/agents/${agentId}/delegate`, req)
      .then((r) => r.data),

  getVersions: (agentId: string) =>
    apiClient.get<AgentVersion[]>(`/internal/agents/${agentId}/versions`).then((r) => r.data),
};
