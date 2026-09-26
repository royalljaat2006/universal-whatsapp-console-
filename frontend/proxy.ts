import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * App-shell Content-Security-Policy. Nonce-based + 'strict-dynamic': Next.js
 * automatically applies this request's nonce to its own inline/bootstrap
 * scripts when it sees the nonce in this header (documented Next.js
 * middleware CSP pattern), and 'strict-dynamic' extends that trust to
 * scripts loaded BY a trusted script — e.g. the Facebook JS SDK tag that
 * features/numbers/embedded-signup-button.tsx creates at runtime via
 * document.createElement("script"), without needing a connect.facebook.net
 * host entry in script-src. `https:` + `'unsafe-inline'` are fallbacks only:
 * browsers that understand nonces/strict-dynamic ignore them (CSP3 spec);
 * only browsers with no nonce support fall back to them.
 *
 * Media (apps/media) already sets its own stricter
 * `sandbox; default-src 'none'` CSP on user-uploaded content responses —
 * unaffected by this header, which only applies to HTML page navigations.
 */
function buildCsp(nonce: string, isDev: boolean): string {
  const scriptSrc = isDev
    ? `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' 'unsafe-eval' https: 'unsafe-inline'`
    : `script-src 'self' 'nonce-${nonce}' 'strict-dynamic' https: 'unsafe-inline'`;
  return [
    `default-src 'self'`,
    scriptSrc,
    // Three components use inline style={{}} — style-src 'unsafe-inline' is
    // the standard pragmatic tradeoff (style injection is a much lower-value
    // XSS vector than script injection, which stays nonce-gated above).
    `style-src 'self' 'unsafe-inline'`,
    `img-src 'self' data: blob:`,
    `font-src 'self'`,
    `connect-src 'self' https://graph.facebook.com https://www.facebook.com https://connect.facebook.net`,
    `frame-src https://www.facebook.com https://connect.facebook.net`,
    `frame-ancestors 'none'`,
    `base-uri 'self'`,
    `form-action 'self'`,
    `object-src 'none'`,
    `upgrade-insecure-requests`,
  ].join("; ");
}

export function proxy(request: NextRequest) {
  const nonce = Buffer.from(crypto.randomUUID()).toString("base64");
  const csp = buildCsp(nonce, process.env.NODE_ENV !== "production");

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-nonce", nonce);
  requestHeaders.set("Content-Security-Policy", csp);

  const response = NextResponse.next({ request: { headers: requestHeaders } });
  response.headers.set("Content-Security-Policy", csp);
  return response;
}

export const config = {
  matcher: [
    // Skip static assets and image optimization; apply to page navigations.
    "/((?!_next/static|_next/image|favicon.ico).*)",
  ],
};
