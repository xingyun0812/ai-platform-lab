import apiClient from "./client";

export interface WorkflowDefinition {
  workflow_id: string;
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface WorkflowNode {
  node_id: string;
  node_type: "start" | "end" | "llm_call" | "agent_call" | "tool_call" | "condition" | "parallel";
  name?: string;
  config?: Record<string, unknown>;
}

export interface WorkflowEdge {
  from_node: string;
  to_node: string;
  condition?: string;
}

export interface ExecuteWorkflowRequest {
  input: Record<string, unknown>;
  timeout?: number;
}

export interface ExecuteWorkflowResult {
  run_id: string;
  workflow_id: string;
  status: "running" | "completed" | "failed" | "timeout";
  output?: Record<string, unknown>;
  error?: string;
  steps: Array<{
    node_id: string;
    status: string;
    output?: unknown;
    latency_ms: number;
  }>;
  total_latency_ms: number;
}

export const orchestratorApi = {
  list: () =>
    apiClient.get<WorkflowDefinition[]>("/internal/orchestrator/workflows").then((r) => r.data),

  get: (workflowId: string) =>
    apiClient.get<WorkflowDefinition>(`/internal/orchestrator/workflows/${workflowId}`).then((r) => r.data),

  create: (def: Omit<WorkflowDefinition, "workflow_id" | "created_at" | "updated_at"> & { workflow_id?: string }) =>
    apiClient.post<WorkflowDefinition>("/internal/orchestrator/workflows", def).then((r) => r.data),

  update: (workflowId: string, patch: Partial<WorkflowDefinition>) =>
    apiClient.patch<WorkflowDefinition>(`/internal/orchestrator/workflows/${workflowId}`, patch).then((r) => r.data),

  delete: (workflowId: string) =>
    apiClient.delete(`/internal/orchestrator/workflows/${workflowId}`),

  execute: (workflowId: string, req: ExecuteWorkflowRequest) =>
    apiClient
      .post<ExecuteWorkflowResult>(`/internal/orchestrator/workflows/${workflowId}/execute`, req)
      .then((r) => r.data),
};
