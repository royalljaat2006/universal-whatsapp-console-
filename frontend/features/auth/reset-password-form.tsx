"use client";

import { EyeIcon, EyeOffIcon, LockIcon } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";

import { ErrorState } from "@/components/ui/states";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingButton } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";

export function ResetPasswordForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const uid = searchParams.get("uid");
  const token = searchParams.get("token");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);

  if (!uid || !token) {
    return (
      <ErrorState
        error={
          new ApiError(
            400,
            "MISSING_TOKEN",
            "This link is incomplete — copy it exactly as it was emailed to you, or request a new one.",
          )
        }
      />
    );
  }

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await authService.confirmPasswordReset({ uid: uid!, token: token!, password });
      setDone(true);
      setTimeout(() => router.replace("/login"), 1800);
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <p className="text-supporting">
        Password updated. Redirecting to sign in…{" "}
        <Link href="/login" className="text-primary hover:underline">
          Go now
        </Link>
        .
      </p>
    );
  }

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-5">
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="password">New password</Label>
        <div className="relative">
          <LockIcon
            aria-hidden
            className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2"
          />
          <Input
            id="password"
            type={showPassword ? "text" : "password"}
            autoComplete="new-password"
            required
            minLength={10}
            autoFocus
            className="h-10 pr-10 pl-9"
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
        <p className="text-caption">At least 10 characters. Signs you out everywhere else.</p>
      </div>
      {error ? <ErrorState error={error} inline /> : null}
      <LoadingButton type="submit" size="lg" className="h-10 w-full" loading={submitting}>
        {submitting ? "Updating…" : "Update password"}
      </LoadingButton>
    </form>
  );
}
