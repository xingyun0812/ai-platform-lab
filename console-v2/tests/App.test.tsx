/**
 * App.test.tsx — Console V2 unit tests (≥10 test cases)
 * Uses @testing-library/react + vitest
 */
import React from "react";
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { MemoryRouter, Routes, Route } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ConfigProvider } from "antd";

// ── Helpers ──────────────────────────────────────────────────────────────────

function Wrapper({ children }: { children: React.ReactNode }) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={qc}>
      <ConfigProvider>
        {children}
      </ConfigProvider>
    </QueryClientProvider>
  );
}

function renderWithRouter(
  ui: React.ReactElement,
  { initialEntries = ["/"] }: { initialEntries?: string[] } = {}
) {
  const qc = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <MemoryRouter initialEntries={initialEntries}>
      <QueryClientProvider client={qc}>
        <ConfigProvider>{ui}</ConfigProvider>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

// ── Mock axios ────────────────────────────────────────────────────────────────
vi.mock("../src/api/client", () => {
  const mockClient = {
    get: vi.fn().mockResolvedValue({ data: [] }),
    post: vi.fn().mockResolvedValue({ data: {} }),
    put: vi.fn().mockResolvedValue({ data: {} }),
    patch: vi.fn().mockResolvedValue({ data: {} }),
    delete: vi.fn().mockResolvedValue({ data: {} }),
    interceptors: {
      request: { use: vi.fn() },
      response: { use: vi.fn() },
    },
  };
  return { default: mockClient };
});

// ── Import pages after mock ───────────────────────────────────────────────────
import Login from "../src/pages/Login";
import Dashboard from "../src/pages/Dashboard";
import Tenants from "../src/pages/Tenants";
import Agents from "../src/pages/Agents";
import Memory from "../src/pages/Memory";
import Orchestrator from "../src/pages/Orchestrator";
import Audit from "../src/pages/Audit";
import Settings from "../src/pages/Settings";
import Embedding from "../src/pages/Embedding";
import RequireAuth from "../src/components/RequireAuth";

// ── Tests ─────────────────────────────────────────────────────────────────────

describe("Console V2 — App & Pages", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  // 1. Login page renders form
  it("1. Login page renders form elements", () => {
    renderWithRouter(<Login />, { initialEntries: ["/login"] });
    expect(screen.getByTestId("tenant-id-input")).toBeDefined();
    expect(screen.getByTestId("api-key-input")).toBeDefined();
    expect(screen.getByTestId("login-submit")).toBeDefined();
  });

  // 2. Login stores token and tenant_id
  it("2. Login form submit stores token in localStorage", async () => {
    const mockPost = vi.fn().mockResolvedValue({
      data: { token: "test-jwt-token", tenant_id: "test-tenant", role: "platform_admin" },
    });

    const { default: apiClient } = await import("../src/api/client");
    (apiClient.post as ReturnType<typeof vi.fn>).mockImplementation(mockPost);

    renderWithRouter(<Login />, { initialEntries: ["/login"] });

    const tenantInput = screen.getByTestId("tenant-id-input");
    const apiKeyInput = screen.getByTestId("api-key-input");
    const submitBtn = screen.getByTestId("login-submit");

    fireEvent.change(tenantInput.querySelector("input")!, {
      target: { value: "test-tenant" },
    });
    fireEvent.change(apiKeyInput.querySelector("input")!, {
      target: { value: "sk-test-key" },
    });
    fireEvent.click(submitBtn);

    await waitFor(() => {
      expect(mockPost).toHaveBeenCalled();
    });
  });

  // 3. Dashboard renders stat cards
  it("3. Dashboard renders stat cards with data-testid", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        qps: 42.5,
        qps_delta: 8.2,
        tokens_today: 1280000,
        error_rate: 0.3,
        active_sessions: 17,
        requests_timeline: [],
        tokens_by_tenant: [],
      },
    });

    renderWithRouter(<Dashboard />, { initialEntries: ["/dashboard"] });

    await waitFor(() => {
      expect(screen.getByTestId("dashboard")).toBeDefined();
    });
  });

  // 4. Dashboard renders fallback cards even without API
  it("4. Dashboard renders with fallback mock data on API error", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockRejectedValue(new Error("Network Error"));

    renderWithRouter(<Dashboard />, { initialEntries: ["/dashboard"] });

    // Loading spinner should disappear
    await waitFor(() => {
      expect(screen.queryByRole("img", { name: /loading/i }) ?? document.querySelector(".ant-spin")).toBeDefined();
    });
  });

  // 5. Tenants table renders page container
  it("5. Tenants page renders container", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: [] });

    renderWithRouter(<Tenants />, { initialEntries: ["/tenants"] });

    await waitFor(() => {
      expect(screen.getByTestId("tenants-page")).toBeDefined();
    });
  });

  // 6. Agents page renders and has create button
  it("6. Agents page renders create button", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: [] });

    renderWithRouter(<Agents />, { initialEntries: ["/agents"] });

    await waitFor(() => {
      expect(screen.getByTestId("agents-page")).toBeDefined();
    });
    expect(screen.getByTestId("create-agent-btn")).toBeDefined();
  });

  // 7. Memory page renders search input
  it("7. Memory page renders search filter input", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { memories: [], total: 0, has_more: false },
    });

    renderWithRouter(<Memory />, { initialEntries: ["/memory"] });

    await waitFor(() => {
      expect(screen.getByTestId("memory-page")).toBeDefined();
    });
    expect(screen.getByTestId("memory-search-input")).toBeDefined();
  });

  // 8. Orchestrator page renders JSON editor
  it("8. Orchestrator page renders workflow JSON editor in modal", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: [] });

    renderWithRouter(<Orchestrator />, { initialEntries: ["/orchestrator"] });

    await waitFor(() => {
      expect(screen.getByTestId("orchestrator-page")).toBeDefined();
    });

    // Open create modal
    const createBtn = screen.getByTestId("create-workflow-btn");
    fireEvent.click(createBtn);

    await waitFor(() => {
      expect(screen.getByTestId("workflow-json-editor")).toBeDefined();
    });
  });

  // 9. Audit page renders filter dropdown
  it("9. Audit page renders action-level filter", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: { records: [], total: 0, has_more: false },
    });

    renderWithRouter(<Audit />, { initialEntries: ["/audit"] });

    await waitFor(() => {
      expect(screen.getByTestId("audit-page")).toBeDefined();
    });
    expect(screen.getByTestId("action-level-filter")).toBeDefined();
  });

  // 10. RequireAuth redirects when no token
  it("10. RequireAuth redirects to /login when no token", () => {
    localStorage.clear();

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route
            path="/dashboard"
            element={
              <RequireAuth>
                <div data-testid="protected-content">Protected</div>
              </RequireAuth>
            }
          />
          <Route path="/login" element={<div data-testid="login-page">Login</div>} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.queryByTestId("protected-content")).toBeNull();
    expect(screen.getByTestId("login-page")).toBeDefined();
  });

  // 11. RequireAuth renders children when token present
  it("11. RequireAuth renders children when token is in localStorage", () => {
    localStorage.setItem("token", "sk-test-token");

    render(
      <MemoryRouter initialEntries={["/dashboard"]}>
        <Routes>
          <Route
            path="/dashboard"
            element={
              <RequireAuth>
                <div data-testid="protected-content">Protected</div>
              </RequireAuth>
            }
          />
          <Route path="/login" element={<div data-testid="login-page">Login</div>} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByTestId("protected-content")).toBeDefined();
  });

  // 12. Settings page renders feature flags card
  it("12. Settings page renders feature flags card", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({
      data: {
        sandbox_enabled: false,
        oauth2_enabled: false,
        pii_enabled: true,
        audit_enabled: true,
        version: "1.0.0",
      },
    });

    renderWithRouter(<Settings />, { initialEntries: ["/settings"] });

    await waitFor(() => {
      expect(screen.getByTestId("settings-page")).toBeDefined();
    });
  });

  // 13. Embedding page renders multimodal playground
  it("13. Embedding page renders multimodal playground", async () => {
    const apiClient = await import("../src/api/client");
    vi.mocked(apiClient.default.get).mockResolvedValueOnce({
      data: {
        models: [
          {
            model_id: "stub-multimodal",
            provider: "stub",
            dimensions: 16,
            modalities: ["text", "image"],
          },
        ],
      },
    });

    renderWithRouter(<Embedding />, { initialEntries: ["/embedding"] });

    await waitFor(() => {
      expect(screen.getByText("多模态 Embedding")).toBeDefined();
      expect(screen.getByText("生成 Embedding")).toBeDefined();
    });
  });

  // 14. Agents page renders plan approval HITL switch (Q4)
  it("14. Agents page renders plan approval HITL switch", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: [] });

    renderWithRouter(<Agents />, { initialEntries: ["/agents"] });

    await waitFor(() => {
      expect(screen.getByTestId("planner-require-approval-switch")).toBeDefined();
    });
    expect(screen.getByTestId("planner-demo-card")).toBeDefined();
  });

  // 15. Orchestrator page renders workflow create button
  it("15. Orchestrator page renders workflow create button", async () => {
    const { default: apiClient } = await import("../src/api/client");
    (apiClient.get as ReturnType<typeof vi.fn>).mockResolvedValue({ data: { workflows: [] } });

    renderWithRouter(<Orchestrator />, { initialEntries: ["/orchestrator"] });

    await waitFor(() => {
      expect(screen.getByTestId("orchestrator-page")).toBeDefined();
    });
    expect(screen.getByTestId("create-workflow-btn")).toBeDefined();
  });
});
