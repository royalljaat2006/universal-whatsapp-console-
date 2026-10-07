import { api } from "@/lib/api/client";
import type { ApiKey, CreatedApiKey } from "@/types/api";

export interface CreateApiKeyInput {
  name: string;
  scopes: string[];
  expires_at?: string | null;
}

export const apiKeyService = {
  list: () => api.get<ApiKey[]>("/api/v1/api-keys/", { noBusiness: true }),
  create: (input: CreateApiKeyInput) =>
    api.post<CreatedApiKey>("/api/v1/api-keys/", input, { noBusiness: true }),
  revoke: (id: string) => api.delete<void>(`/api/v1/api-keys/${id}/`, { noBusiness: true }),
};
