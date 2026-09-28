import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { headers } from "next/headers";

import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "WhatsApp Console",
  description: "Multi-tenant WhatsApp management platform",
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: LayoutProps<"/">) {
  // Reading the per-request nonce here (set by proxy.ts) is what makes
  // Next.js apply it to its own bootstrap/hydration scripts, and — just as
  // importantly — what forces this layout to render dynamically per request
  // instead of being statically prerendered once at build time. A nonce
  // embedded in a cached/static page would never match the CSP header on a
  // later request, and every script on the page would be silently blocked
  // (see https://nextjs.org/docs/app/guides/content-security-policy).
  const nonce = (await headers()).get("x-nonce") ?? undefined;
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
      data-csp-nonce={nonce}
    >
      <body className="min-h-full">{children}</body>
    </html>
  );
}
