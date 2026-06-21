import apiClient from "./client";

export interface ChatMessage {
  role: "user" | "assistant" | "system";
  content: string;
}

export interface ChatRequest {
  messages: ChatMessage[];
  model?: string;
  stream?: boolean;
  max_tokens?: number;
}

export interface ChatResponse {
  id: string;
  choices: Array<{
    message: ChatMessage;
    finish_reason: string;
  }>;
  usage?: {
    prompt_tokens: number;
    completion_tokens: number;
    total_tokens: number;
  };
}

export const chatApi = {
  complete: (req: ChatRequest) =>
    apiClient.post<ChatResponse>("/v1/chat/completions", req).then((r) => r.data),

  streamComplete: (req: ChatRequest, onChunk: (chunk: string) => void) => {
    return apiClient.post("/v1/chat/completions", { ...req, stream: true }, {
      responseType: "stream",
      onDownloadProgress: (progressEvent) => {
        const chunk = progressEvent.event?.target?.responseText ?? "";
        onChunk(chunk);
      },
    });
  },
};
