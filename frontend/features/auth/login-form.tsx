"use client";

import { EyeIcon, EyeOffIcon, LockIcon, MailIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { ErrorState } from "@/components/ui/states";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingButton } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await authService.login(email.trim(), password);
      router.replace("/");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setSubmitting(false);
    }
  }

  const invalid = Boolean(error && error.status >= 400 && error.status < 500);

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="email">Work email</Label>
        <div className="relative">
          <MailIcon
            aria-hidden
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
          />
          <Input
            id="email"
            type="email"
            autoComplete="username"
            placeholder="you@company.com"
            required
            autoFocus
            className="h-10 pl-9"
            aria-invalid={invalid || undefined}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="password">Password</Label>
        <div className="relative">
          <LockIcon
            aria-hidden
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
          />
          <Input
            id="password"
            type={showPassword ? "text" : "password"}
            autoComplete="current-password"
            required
            className="h-10 pr-10 pl-9"
            aria-invalid={invalid || undefined}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <button
            type="button"
            onClick={() => setShowPassword((v) => !v)}
            aria-label={showPassword ? "Hide password" : "Show password"}
            aria-pressed={showPassword}
            className="text-muted-foreground hover:text-foreground focus-visible:ring-ring/50 absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 outline-none focus-visible:ring-2"
          >
            {showPassword ? (
              <EyeOffIcon aria-hidden className="size-4" />
            ) : (
              <EyeIcon aria-hidden className="size-4" />
            )}
          </button>
        </div>
      </div>
      {error ? <ErrorState error={error} inline /> : null}
      <LoadingButton type="submit" size="lg" className="h-10 w-full" loading={submitting}>
        {submitting ? "Signing in…" : "Sign in"}
      </LoadingButton>
    </form>
  );
}
