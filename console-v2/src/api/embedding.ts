import apiClient from "./client";

export interface EmbeddingRequest {
  input: string | string[];
  model?: string;
}

export interface EmbeddingResponse {
  object: "list";
  data: Array<{
    object: "embedding";
    embedding: number[];
    index: number;
  }>;
  model: string;
  usage: {
    prompt_tokens: number;
    total_tokens: number;
  };
}

export interface EmbeddingModel {
  model_id: string;
  provider: string;
  dimensions: number;
  max_input_tokens: number;
  enabled: boolean;
}

export const embeddingApi = {
  embed: (req: EmbeddingRequest) =>
    apiClient.post<EmbeddingResponse>("/v1/embeddings", req).then((r) => r.data),

  listModels: () =>
    apiClient.get<EmbeddingModel[]>("/internal/embedding/models").then((r) => r.data),
};
