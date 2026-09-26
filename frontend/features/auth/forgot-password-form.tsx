"use client";

import { MailIcon } from "lucide-react";
import { useState } from "react";

import { ErrorState } from "@/components/ui/states";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingButton } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await authService.requestPasswordReset(email.trim());
      // Always shows the same confirmation, whether or not the email is a
      // real account — the backend never discloses which (see
      // apps.identity.api.RequestPasswordResetView).
      setSent(true);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setSubmitting(false);
    }
  }

  if (sent) {
    return (
      <p className="text-supporting">
        If an account exists for <span className="font-medium">{email}</span>, a reset link is on its
        way. It works once and expires soon.
      </p>
    );
  }

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
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
      </div>
      {error ? <ErrorState error={error} inline /> : null}
      <LoadingButton type="submit" size="lg" className="h-10 w-full" loading={submitting}>
        {submitting ? "Sending…" : "Send reset link"}
      </LoadingButton>
    </form>
  );
}
