import { ApiError, toApiError } from "./errors";

/**
 * Typed fetch wrapper for the backend API.
 *
 * - Same-origin requests with the session cookie (no tokens in JS).
 * - Echoes the CSRF cookie on unsafe methods.
 * - Sends X-Organization-ID (tenant) and X-Business-ID (workspace) headers;
 *   the server validates both — they select context, they never grant access.
 * - Converts every failure into an ApiError carrying the backend request_id.
 *
 * The browser never talks to Meta: provider credentials exist only on the
 * server (docs/security.md).
 */

const UNSAFE_METHODS = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// Set only when reverse-proxied under a subpath (see next.config.ts). Not a
// secret — just the public mount point — so NEXT_PUBLIC_ is safe here.
const BASE_PATH = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

function withBasePath(path: string): string {
  return BASE_PATH ? `${BASE_PATH}${path}` : path;
}

let activeOrganizationId: string | null = null;
let activeBusinessId: string | null = null;

export function setActiveOrganization(id: string | null): void {
  activeOrganizationId = id;
}

export function setActiveBusiness(id: string | null): void {
  activeBusinessId = id;
}

export function readCookie(name: string, cookieString?: string): string | null {
  const source = cookieString ?? (typeof document !== "undefined" ? document.cookie : "");
  for (const part of source.split(";")) {
    const [key, ...rest] = part.trim().split("=");
    if (key === name) return decodeURIComponent(rest.join("="));
  }
  return null;
}

export interface RequestOptions {
  method?: string;
  body?: unknown;
  query?: Record<string, string | number | undefined>;
  headers?: Record<string, string>;
  /** Skip X-Organization-ID and X-Business-ID (non-tenant endpoints like auth). */
  unscoped?: boolean;
  /** Skip only X-Business-ID (organization-wide endpoints like the business list). */
  noBusiness?: boolean;
  signal?: AbortSignal;
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const url = new URL(withBasePath(path), "http://placeholder");
  for (const [key, value] of Object.entries(query ?? {})) {
    if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
  }
  return url.pathname + url.search;
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = (options.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = { Accept: "application/json", ...options.headers };
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (UNSAFE_METHODS.has(method)) {
    const csrf = readCookie("csrftoken");
    if (csrf) headers["X-CSRFToken"] = csrf;
  }
  if (!options.unscoped && activeOrganizationId) {
    headers["X-Organization-ID"] = activeOrganizationId;
    if (!options.noBusiness && activeBusinessId) headers["X-Business-ID"] = activeBusinessId;
  }

  let response: Response;
  try {
    response = await fetch(buildUrl(path, options.query), {
      method,
      headers,
      body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
      credentials: "same-origin",
      signal: options.signal,
    });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "NETWORK_ERROR", "The server could not be reached.");
  }

  if (!response.ok) throw await toApiError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  get: <T>(path: string, options?: Omit<RequestOptions, "method" | "body">) =>
    apiRequest<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    apiRequest<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, "method" | "body">) =>
    apiRequest<T>(path, { ...options, method: "PATCH", body }),
  delete: <T>(path: string, options?: Omit<RequestOptions, "method" | "body">) =>
    apiRequest<T>(path, { ...options, method: "DELETE" }),
};

/** Multipart upload (the browser sets the multipart boundary itself). */
export async function apiUpload<T>(path: string, form: FormData): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  const csrf = readCookie("csrftoken");
  if (csrf) headers["X-CSRFToken"] = csrf;
  if (activeOrganizationId) headers["X-Organization-ID"] = activeOrganizationId;
  if (activeBusinessId) headers["X-Business-ID"] = activeBusinessId;
  let response: Response;
  try {
    response = await fetch(withBasePath(path), { method: "POST", headers, body: form, credentials: "same-origin" });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "The server could not be reached.");
  }
  if (!response.ok) throw await toApiError(response);
  return (await response.json()) as T;
}

/**
 * Fetch a protected binary (media content) with the tenant headers. <img src>
 * cannot send X-Organization-ID, so media is loaded as a Blob and shown via an
 * object URL; the server still enforces organization + business scope.
 */
export async function apiBlob(path: string, signal?: AbortSignal): Promise<Blob> {
  const headers: Record<string, string> = {};
  if (activeOrganizationId) headers["X-Organization-ID"] = activeOrganizationId;
  if (activeBusinessId) headers["X-Business-ID"] = activeBusinessId;
  let response: Response;
  try {
    response = await fetch(withBasePath(path), { headers, credentials: "same-origin", signal });
  } catch (cause) {
    if (cause instanceof DOMException && cause.name === "AbortError") throw cause;
    throw new ApiError(0, "NETWORK_ERROR", "The server could not be reached.");
  }
  if (!response.ok) throw await toApiError(response);
  return response.blob();
}
