import axios, { AxiosError } from "axios";
import { message } from "antd";

const BASE_URL = import.meta.env.VITE_API_BASE ?? "/";
const CONSOLE_BASE = import.meta.env.BASE_URL.replace(/\/$/, "") || "/console";

const loginPath = () => `${CONSOLE_BASE}/login`;

const apiClient = axios.create({
  baseURL: BASE_URL,
  timeout: 30_000,
  headers: {
    "Content-Type": "application/json",
  },
});

// Request interceptor: inject auth headers
apiClient.interceptors.request.use(
  (config) => {
    const token = localStorage.getItem("token");
    const tenantId = localStorage.getItem("tenant_id");

    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }
    if (tenantId) {
      config.headers["X-Tenant-Id"] = tenantId;
    }
    return config;
  },
  (error) => Promise.reject(error)
);

// Response interceptor: handle errors
apiClient.interceptors.response.use(
  (response) => response,
  (error: AxiosError) => {
    if (error.response?.status === 401) {
      localStorage.removeItem("token");
      localStorage.removeItem("tenant_id");
      window.location.href = loginPath();
      return Promise.reject(error);
    }

    const data = error.response?.data as Record<string, unknown> | undefined;
    const errMsg =
      (data?.detail as string) ||
      (data?.message as string) ||
      error.message ||
      "请求失败，请稍后重试";

    if (error.response?.status !== 404) {
      message.error(errMsg);
    }

    return Promise.reject(error);
  }
);

export default apiClient;
