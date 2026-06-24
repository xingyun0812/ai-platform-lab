import apiClient from "./client";

export type EmbeddingInputItem =
  | { type: "text"; text: string }
  | { type: "image_url"; url: string }
  | { type: "image_base64"; mime: string; data: string };

export interface EmbedRequest {
  model_id: string;
  texts?: string[];
  inputs?: EmbeddingInputItem[];
  tenant_id?: string;
}

export interface EmbedResponse {
  model_id: string;
  embeddings: number[][];
  dimensions: number;
  usage: Record<string, number>;
  cached: number;
}

export interface EmbeddingModel {
  model_id: string;
  name?: string;
  provider: string;
  dimensions: number;
  max_input_tokens?: number;
  modalities?: string[];
  enabled?: boolean;
}

export const embeddingApi = {
  embed: (req: EmbedRequest) =>
    apiClient.post<EmbedResponse>("/internal/embeddings/embed", req).then((r) => r.data),

  listModels: () =>
    apiClient
      .get<{ models: EmbeddingModel[] }>("/internal/embeddings/models")
      .then((r) => r.data.models ?? []),
};
