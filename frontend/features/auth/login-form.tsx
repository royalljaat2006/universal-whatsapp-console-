"use client";

import { EyeIcon, EyeOffIcon, LockIcon, MailIcon, ShieldCheckIcon } from "lucide-react";
import Link from "next/link";
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
  // Set once the password was accepted but a second factor is still required.
  const [challenge, setChallenge] = useState(false);
  const [useRecovery, setUseRecovery] = useState(false);
  const [secondFactor, setSecondFactor] = useState("");

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const result = await authService.login(email.trim(), password);
      if (result && "mfa_required" in result && result.mfa_required) {
        setChallenge(true);
        return;
      }
      router.replace("/");
    } catch (cause) {
      setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setSubmitting(false);
    }
  }

  async function onVerify(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const value = secondFactor.trim();
      await authService.verifyMfa(useRecovery ? { recovery_code: value } : { code: value });
      router.replace("/");
    } catch (cause) {
      const apiError =
        cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));
      // The attempt expired or was locked: the password step must be redone.
      if (apiError.code === "MFA_SESSION_EXPIRED") {
        setChallenge(false);
        setSecondFactor("");
      }
      setError(apiError);
    } finally {
      setSubmitting(false);
    }
  }

  const invalid = Boolean(error && error.status >= 400 && error.status < 500);

  if (challenge) {
    return (
      <form onSubmit={onVerify} className="flex flex-col gap-5">
        <div className="flex items-start gap-3">
          <ShieldCheckIcon aria-hidden className="text-primary mt-0.5 size-5 shrink-0" />
          <div>
            <h2 className="text-section-title">Two-step verification</h2>
            <p className="text-supporting">
              {useRecovery
                ? "Enter one of your unused recovery codes."
                : "Enter the 6-digit code from your authenticator app."}
            </p>
          </div>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="second-factor">
            {useRecovery ? "Recovery code" : "Authentication code"}
          </Label>
          <Input
            id="second-factor"
            autoComplete="one-time-code"
            inputMode={useRecovery ? "text" : "numeric"}
            required
            autoFocus
            maxLength={useRecovery ? 24 : 7}
            placeholder={useRecovery ? "xxxxx-xxxxx" : "123456"}
            className="h-10 font-mono tracking-widest"
            aria-invalid={invalid || undefined}
            value={secondFactor}
            onChange={(e) => setSecondFactor(e.target.value)}
          />
        </div>
        {error ? <ErrorState error={error} inline /> : null}
        <LoadingButton type="submit" size="lg" className="h-10 w-full" loading={submitting}>
          {submitting ? "Verifying…" : "Verify and sign in"}
        </LoadingButton>
        <div className="flex justify-between">
          <button
            type="button"
            className="text-caption text-primary hover:underline"
            onClick={() => {
              setUseRecovery((v) => !v);
              setSecondFactor("");
              setError(null);
            }}
          >
            {useRecovery ? "Use authenticator code" : "Use a recovery code"}
          </button>
          <button
            type="button"
            className="text-caption text-muted-foreground hover:underline"
            onClick={() => {
              setChallenge(false);
              setSecondFactor("");
              setError(null);
            }}
          >
            Back
          </button>
        </div>
      </form>
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
            aria-invalid={invalid || undefined}
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between">
          <Label htmlFor="password">Password</Label>
          <Link href="/forgot-password" className="text-caption text-primary hover:underline">
            Forgot password?
          </Link>
        </div>
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
