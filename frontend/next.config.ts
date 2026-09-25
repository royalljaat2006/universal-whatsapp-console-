import type { NextConfig } from "next";

// Server-side only (never NEXT_PUBLIC_): where the dev server proxies API calls
// when the app runs outside nginx. In docker/production nginx routes /api,
// /health and /ws to Django directly and these rewrites are never hit.
const backend = process.env.BACKEND_INTERNAL_URL ?? "http://localhost:8000";

// Set only when the app is reverse-proxied under a subpath (e.g. "/whatsapp-console").
// Must match NEXT_PUBLIC_BASE_PATH used by lib/api/client.ts and lib/realtime/client.ts.
const basePath = process.env.BASE_PATH || undefined;

const nextConfig: NextConfig = {
  reactCompiler: true,
  output: "standalone",
  poweredByHeader: false,
  basePath,
  // The floating dev badge overlaps the sidebar during visual QA.
  devIndicators: false,
  async rewrites() {
    return [
      { source: "/api/:path*", destination: `${backend}/api/:path*` },
      { source: "/health/:path*", destination: `${backend}/health/:path*` },
    ];
  },
};

export default nextConfig;
