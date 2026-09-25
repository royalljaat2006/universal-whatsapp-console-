import type { ApiErrorBody } from "@/types/api";

/** Normalised error for every failed API call (mirrors the backend contract). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: Record<string, unknown>;
  readonly requestId: string | null;

  constructor(
    status: number,
    code: string,
    message: string,
    details: Record<string, unknown> = {},
    requestId: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
    this.requestId = requestId;
  }

  get isAuthError(): boolean {
    return this.status === 401;
  }
}

function isErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== "object" || value === null || !("error" in value)) return false;
  const error = (value as { error: unknown }).error;
  return typeof error === "object" && error !== null && "code" in error;
}

export async function toApiError(response: Response): Promise<ApiError> {
  const headerRequestId = response.headers.get("X-Request-ID");
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    // Non-JSON error (e.g. proxy 502 HTML page).
  }
  if (isErrorBody(body)) {
    const { code, message, details, request_id } = body.error;
    return new ApiError(response.status, code, message, details, request_id ?? headerRequestId);
  }
  return new ApiError(
    response.status,
    response.status >= 500 ? "UPSTREAM_UNAVAILABLE" : "UNEXPECTED_RESPONSE",
    `Request failed with status ${response.status}`,
    {},
    headerRequestId,
  );
}
