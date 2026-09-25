import { InboxIcon, LayersIcon, ShieldCheckIcon } from "lucide-react";

import { LogoMark } from "@/components/brand/logo";
import { LoginForm } from "@/features/auth/login-form";

const CAPABILITIES = [
  { icon: InboxIcon, text: "One shared inbox for every connected WhatsApp number" },
  { icon: LayersIcon, text: "Approved templates, campaigns and delivery analytics" },
  { icon: ShieldCheckIcon, text: "Business-level access control with a full audit trail" },
];

/** Abstract conversation motif — decorative, conveys no data. */
function ConversationMotif() {
  return (
    <svg viewBox="0 0 520 360" aria-hidden className="w-full max-w-lg opacity-90">
      <defs>
        <linearGradient id="bubble" x1="0" x2="1">
          <stop offset="0" stopColor="white" stopOpacity="0.14" />
          <stop offset="1" stopColor="white" stopOpacity="0.05" />
        </linearGradient>
      </defs>
      <g fill="url(#bubble)" stroke="white" strokeOpacity="0.12">
        <rect x="20" y="30" width="260" height="56" rx="16" />
        <rect x="200" y="110" width="300" height="72" rx="16" />
        <rect x="20" y="206" width="220" height="56" rx="16" />
        <rect x="260" y="286" width="210" height="48" rx="16" />
      </g>
      <g fill="white" fillOpacity="0.28">
        <rect x="40" y="50" width="150" height="8" rx="4" />
        <rect x="40" y="66" width="96" height="8" rx="4" />
        <rect x="222" y="132" width="220" height="8" rx="4" />
        <rect x="222" y="148" width="170" height="8" rx="4" />
        <rect x="40" y="226" width="130" height="8" rx="4" />
        <rect x="280" y="306" width="120" height="8" rx="4" />
      </g>
      <g
        fill="none"
        stroke="var(--brand)"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.9"
      >
        <path d="M456 166l5 5 9-10" />
        <path d="M466 166l5 5 9-10" />
        <path d="M430 318l5 5 9-10" />
      </g>
    </svg>
  );
}

export default function LoginPage() {
  return (
    <main className="bg-background grid min-h-screen lg:grid-cols-[55fr_45fr]">
      <section className="relative hidden overflow-hidden bg-[oklch(0.24_0.035_172)] text-white lg:flex lg:flex-col lg:justify-between lg:p-12 xl:p-16">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0 bg-[radial-gradient(60rem_40rem_at_120%_-10%,oklch(0.45_0.1_165/0.55),transparent),radial-gradient(40rem_30rem_at_-20%_110%,oklch(0.35_0.07_200/0.5),transparent)]"
        />
        <div className="relative flex items-center gap-3">
          <LogoMark className="size-9" />
          <span className="text-base font-semibold tracking-tight">WhatsApp Management Console</span>
        </div>
        <div className="relative flex flex-col gap-8">
          <div className="flex max-w-xl flex-col gap-4">
            <h1 className="text-display text-white">Every business conversation, in one calm workspace.</h1>
            <p className="max-w-md text-[15px] leading-6 text-white/75">
              Manage conversations, WhatsApp numbers, templates and messaging operations from one place.
            </p>
          </div>
          <ConversationMotif />
        </div>
        <ul className="relative flex flex-col gap-3 text-sm text-white/80">
          {CAPABILITIES.map(({ icon: Icon, text }) => (
            <li key={text} className="flex items-center gap-3">
              <span className="flex size-7 items-center justify-center rounded-md bg-white/10">
                <Icon aria-hidden className="size-4" />
              </span>
              {text}
            </li>
          ))}
        </ul>
      </section>

      <section className="flex flex-col justify-center px-6 py-10 sm:px-12">
        <div className="mx-auto flex w-full max-w-sm flex-col gap-8">
          <div className="flex flex-col gap-5">
            <LogoMark className="size-10" />
            <div>
              <h2 className="text-page-title">Welcome back</h2>
              <p className="text-supporting mt-1">Sign in to your workspace</p>
            </div>
          </div>
          <LoginForm />
          <p className="text-caption">
            Access is managed by your workspace administrator. Contact them if you need an account or a
            password reset.
          </p>
        </div>
      </section>
    </main>
  );
}
