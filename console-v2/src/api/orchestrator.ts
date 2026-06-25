import apiClient from "./client";

export interface WorkflowDefinition {
  workflow_id: string;
  name: string;
  description?: string;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  enabled?: boolean;
  created_at?: string;
  updated_at?: string;
  start_node?: string;
  end_node?: string;
}

export interface WorkflowNode {
  node_id: string;
  node_type: "start" | "end" | "llm_call" | "agent_call" | "tool_call" | "condition" | "parallel" | "output";
  name?: string;
  config?: Record<string, unknown>;
}

export interface WorkflowEdge {
  from_node: string;
  to_node: string;
  condition?: string;
}

export interface ExecuteWorkflowRequest {
  inputs?: Record<string, unknown>;
  max_steps?: number;
  timeout_seconds?: number;
  execution_id?: string;
  resume?: boolean;
}

export interface ExecuteWorkflowResult {
  workflow_id: string;
  execution_id?: string | null;
  status: string;
  final_output?: unknown;
  outputs?: Record<string, unknown>;
  trace?: Array<Record<string, unknown>>;
  error?: string | null;
  execution_time_ms?: number;
  resumed?: boolean;
}

export interface ExecutionCheckpoint {
  execution_id: string;
  tenant_id: string;
  workflow_id: string;
  status: string;
  current_node: string | null;
  inputs: Record<string, unknown>;
  outputs: Record<string, unknown>;
  variables: Record<string, unknown>;
  trace: Array<Record<string, unknown>>;
  error?: string | null;
  created_at?: number;
  updated_at?: number;
}

export const orchestratorApi = {
  list: () =>
    apiClient
      .get<{ workflows: WorkflowDefinition[] }>("/internal/orchestrator/workflows")
      .then((r) => r.data.workflows ?? []),

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
      .post<ExecuteWorkflowResult>(`/internal/orchestrator/workflows/${workflowId}/execute`, {
        inputs: req.inputs ?? {},
        max_steps: req.max_steps,
        timeout_seconds: req.timeout_seconds,
        execution_id: req.execution_id,
        resume: req.resume ?? false,
      })
      .then((r) => r.data),

  getExecution: (executionId: string) =>
    apiClient
      .get<ExecutionCheckpoint>(`/internal/orchestrator/executions/${executionId}`)
      .then((r) => r.data),

  resumeExecution: (executionId: string, inputs?: Record<string, unknown>) =>
    apiClient
      .post<ExecuteWorkflowResult>(`/internal/orchestrator/executions/${executionId}/resume`, {
        inputs: inputs ?? {},
      })
      .then((r) => r.data),
};
