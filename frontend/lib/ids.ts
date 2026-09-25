/** Random idempotency key. `crypto.randomUUID` exists only in secure contexts
 * (HTTPS/localhost); `getRandomValues` works everywhere. */
export function newIdempotencyKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
