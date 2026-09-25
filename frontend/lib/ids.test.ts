import { afterEach, describe, expect, it, vi } from "vitest";

import { newIdempotencyKey } from "./ids";

describe("newIdempotencyKey", () => {
  afterEach(() => vi.restoreAllMocks());

  it("produces unique keys accepted by the backend (8-128 safe chars)", () => {
    const keys = new Set(Array.from({ length: 200 }, () => newIdempotencyKey()));
    expect(keys.size).toBe(200);
    for (const key of keys) expect(key).toMatch(/^[A-Za-z0-9._:-]{8,128}$/);
  });

  it("falls back to getRandomValues outside secure contexts", () => {
    const descriptor = Object.getOwnPropertyDescriptor(crypto, "randomUUID");
    Object.defineProperty(crypto, "randomUUID", { value: undefined, configurable: true });
    try {
      expect(newIdempotencyKey()).toMatch(/^[0-9a-f]{32}$/);
    } finally {
      if (descriptor) Object.defineProperty(crypto, "randomUUID", descriptor);
      else delete (crypto as { randomUUID?: unknown }).randomUUID;
    }
  });
});
