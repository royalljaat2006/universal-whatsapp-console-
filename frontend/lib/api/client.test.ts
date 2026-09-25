import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { apiRequest, readCookie, setActiveOrganization } from "./client";
import { ApiError } from "./errors";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  document.cookie = "csrftoken=csrf-123";
  setActiveOrganization(null);
});

afterEach(() => {
  fetchMock.mockReset();
  vi.unstubAllGlobals();
});

function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });
}

describe("apiRequest", () => {
  it("sends tenant header and CSRF token on unsafe methods", async () => {
    setActiveOrganization("org-1");
    fetchMock.mockResolvedValue(jsonResponse(202, { id: "m1" }));

    await apiRequest("/api/v1/messages/", { method: "POST", body: { a: 1 } });

    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/messages/");
    expect(init.headers["X-Organization-ID"]).toBe("org-1");
    expect(init.headers["X-CSRFToken"]).toBe("csrf-123");
    expect(init.credentials).toBe("same-origin");
  });

  it("omits the tenant header for unscoped calls and CSRF on GET", async () => {
    setActiveOrganization("org-1");
    fetchMock.mockResolvedValue(jsonResponse(200, {}));
    await apiRequest("/api/v1/auth/me/", { unscoped: true });
    const [, init] = fetchMock.mock.calls[0];
    expect(init.headers["X-Organization-ID"]).toBeUndefined();
    expect(init.headers["X-CSRFToken"]).toBeUndefined();
  });

  it("serialises query parameters, skipping empty values", async () => {
    fetchMock.mockResolvedValue(jsonResponse(200, {}));
    await apiRequest("/api/v1/whatsapp/numbers/", { query: { status: "connected", business_account: "" } });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/whatsapp/numbers/?status=connected");
  });

  it("maps the backend error contract to ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(422, {
        error: { code: "SERVICE_WINDOW_CLOSED", message: "closed", details: {}, request_id: "req-1" },
      }),
    );
    const error = await apiRequest("/x").catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 422, code: "SERVICE_WINDOW_CLOSED", requestId: "req-1" });
  });

  it("maps non-JSON upstream failures", async () => {
    fetchMock.mockResolvedValue(
      new Response("<html>bad gateway</html>", { status: 502, headers: { "X-Request-ID": "r" } }),
    );
    const error = (await apiRequest("/x").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("UPSTREAM_UNAVAILABLE");
    expect(error.requestId).toBe("r");
  });

  it("maps network failures", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await apiRequest("/x").catch((e: unknown) => e)) as ApiError;
    expect(error.code).toBe("NETWORK_ERROR");
  });

  it("returns undefined for 204", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(apiRequest("/x", { method: "POST" })).resolves.toBeUndefined();
  });
});

describe("readCookie", () => {
  it("parses and decodes a named cookie", () => {
    expect(readCookie("b", "a=1; b=x%20y; c=3")).toBe("x y");
    expect(readCookie("missing", "a=1")).toBeNull();
  });
});
