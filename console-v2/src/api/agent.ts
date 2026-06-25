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

export interface PlanStep {
  id: string;
  description: string;
  tool_hint?: string | null;
  agent_hint?: string | null;
  depends_on?: string[];
}

export interface AgentPlan {
  goal: string;
  steps: PlanStep[];
}

export interface AgentPlanResponse {
  tenant_id: string;
  goal: string;
  plan: AgentPlan;
  model: string;
  trace_id?: string | null;
}

export interface AgentRunResponse {
  tenant_id: string;
  session_id: string;
  final_message: string;
  tool_calls: Array<{
    tool_name: string;
    status: string;
    result?: string | null;
  }>;
  steps: number;
  model: string;
  status: string;
  plan_approval_id?: string | null;
  plan_summary?: string | null;
  plan?: AgentPlan | null;
  plan_steps_completed?: number | null;
  reasoning_trace?: Array<{ step: number; thinking?: string | null }> | null;
  resumed_from_plan_approval_id?: string | null;
}

export interface PlanApprovalStatus {
  plan_approval_id: string;
  tenant_id: string;
  status: string;
  created_at?: number;
  plan: AgentPlan | null;
}

export interface BlackboardEntry {
  agent_id?: string;
  role?: string;
  content?: string;
  created_at?: string;
}

export interface BlackboardResponse {
  tenant_id: string;
  session_id: string;
  entries: BlackboardEntry[];
  count: number;
}

export const agentApi = {
  list: () =>
    apiClient
      .get<{ agents: AgentSpec[] }>("/internal/agents")
      .then((r) => r.data.agents ?? []),

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

  /** Phase O — Task Planner / auto_plan / 黑板 */
  createPlan: (tenantId: string, goal: string) =>
    apiClient
      .post<AgentPlanResponse>("/v1/agent/plan", {
        tenant_id: tenantId,
        goal,
      })
      .then((r) => r.data),

  runAutoPlan: (
    tenantId: string,
    sessionId: string,
    goal: string,
    model = "chat-fast",
    options?: { requirePlanApproval?: boolean }
  ) =>
    apiClient
      .post<AgentRunResponse>(
        "/v1/agent/run",
        {
          tenant_id: tenantId,
          session_id: sessionId,
          auto_plan: true,
          goal,
          model,
          require_plan_approval: options?.requirePlanApproval ?? false,
        },
        { timeout: 180_000 }
      )
      .then((r) => r.data),

  resumePlanApproval: (
    tenantId: string,
    sessionId: string,
    planApprovalId: string,
    model = "chat-fast"
  ) =>
    apiClient
      .post<AgentRunResponse>(
        "/v1/agent/run",
        {
          tenant_id: tenantId,
          session_id: sessionId,
          plan_approval_id: planApprovalId,
          model,
        },
        { timeout: 180_000 }
      )
      .then((r) => r.data),

  getPlanApproval: (planApprovalId: string) =>
    apiClient
      .get<PlanApprovalStatus>(`/v1/agent/plan/approval/${planApprovalId}`)
      .then((r) => r.data),

  approvePlan: (planApprovalId: string) =>
    apiClient
      .post<{ plan_approval_id: string; status: string }>(
        `/v1/agent/plan/approval/${planApprovalId}/approve`
      )
      .then((r) => r.data),

  rejectPlan: (planApprovalId: string) =>
    apiClient
      .post<{ plan_approval_id: string; status: string }>(
        `/v1/agent/plan/approval/${planApprovalId}/reject`
      )
      .then((r) => r.data),

  getBlackboard: (sessionId: string) =>
    apiClient.get<BlackboardResponse>(`/v1/agent/blackboard/${sessionId}`).then((r) => r.data),
};
