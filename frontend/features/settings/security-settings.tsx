"use client";

import { CheckIcon, CopyIcon, ShieldCheckIcon } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Panel } from "@/components/ui/page";
import { LoadingButton } from "@/components/ui/spinner";
import { ErrorState } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useApiQuery } from "@/hooks/use-api-query";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";
import type { MfaEnrolment } from "@/types/api";

function asApiError(cause: unknown): ApiError {
  return cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));
}

/** Recovery codes are shown exactly once. The list is kept only in component
 * state (never persisted) and is dropped when the user dismisses it. */
function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const [copied, setCopied] = useState(false);
  async function copy() {
    try {
      await navigator.clipboard.writeText(codes.join("\n"));
      setCopied(true);
    } catch {
      // Clipboard unavailable: the codes remain selectable on screen.
    }
  }
  return (
    <div className="flex flex-col gap-3" role="region" aria-label="Recovery codes">
      <p className="text-supporting">
        Save these recovery codes somewhere safe. Each works once if you lose your authenticator app.{" "}
        <strong>They will not be shown again.</strong>
      </p>
      <ul className="bg-surface-muted grid grid-cols-2 gap-x-6 gap-y-1 rounded-md border p-3 font-mono text-sm">
        {codes.map((code) => (
          <li key={code}>{code}</li>
        ))}
      </ul>
      <div className="flex gap-2">
        <Button type="button" variant="outline" size="sm" onClick={() => void copy()}>
          {copied ? <CheckIcon aria-hidden /> : <CopyIcon aria-hidden />}
          {copied ? "Copied" : "Copy codes"}
        </Button>
        <Button type="button" size="sm" onClick={onDone}>
          I&apos;ve saved them
        </Button>
      </div>
    </div>
  );
}

function Enrol({ onEnabled }: { onEnabled: (codes: string[]) => void }) {
  const [password, setPassword] = useState("");
  const [enrolment, setEnrolment] = useState<MfaEnrolment | null>(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  async function start(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setEnrolment(await authService.mfaSetup(password));
      setPassword("");
    } catch (cause) {
      setError(asApiError(cause));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const result = await authService.mfaConfirm(code.trim());
      onEnabled(result.recovery_codes);
    } catch (cause) {
      setError(asApiError(cause));
    } finally {
      setBusy(false);
    }
  }

  if (!enrolment) {
    return (
      <form onSubmit={(e) => void start(e)} className="flex max-w-sm flex-col gap-3">
        <p className="text-supporting">
          Add a second step to sign-in using an authenticator app (Google Authenticator, 1Password, Authy, …).
          Confirm your password to begin.
        </p>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="mfa-password">Current password</Label>
          <Input
            id="mfa-password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        {error ? <ErrorState error={error} inline /> : null}
        <LoadingButton type="submit" loading={busy} className="w-fit">
          Set up two-step verification
        </LoadingButton>
      </form>
    );
  }

  return (
    <form onSubmit={(e) => void confirm(e)} className="flex max-w-md flex-col gap-4">
      <ol className="text-supporting flex list-decimal flex-col gap-2 pl-5">
        <li>
          In your authenticator app choose <em>Add account</em> and enter this key (or{" "}
          <a className="text-primary underline" href={enrolment.otpauth_uri}>
            open it on this device
          </a>
          ):
          <code className="bg-surface-muted mt-1 block rounded border px-2 py-1.5 font-mono text-sm break-all select-all">
            {enrolment.secret}
          </code>
        </li>
        <li>Enter the 6-digit code it shows to finish.</li>
      </ol>
      <div className="flex flex-col gap-1.5">
        <Label htmlFor="mfa-confirm-code">Authentication code</Label>
        <Input
          id="mfa-confirm-code"
          inputMode="numeric"
          autoComplete="one-time-code"
          maxLength={7}
          required
          className="font-mono tracking-widest"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
      </div>
      {error ? <ErrorState error={error} inline /> : null}
      <LoadingButton type="submit" loading={busy} className="w-fit">
        Turn on two-step verification
      </LoadingButton>
    </form>
  );
}

function Manage({
  remaining,
  onChanged,
  onNewCodes,
}: {
  remaining: number;
  onChanged: () => void;
  onNewCodes: (codes: string[]) => void;
}) {
  const toast = useToast();
  const [mode, setMode] = useState<"idle" | "disable" | "regenerate">("idle");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);

  function reset() {
    setMode("idle");
    setPassword("");
    setCode("");
    setUseRecovery(false);
    setError(null);
  }

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const value = code.trim();
      if (mode === "disable") {
        await authService.mfaDisable({
          password,
          ...(useRecovery ? { recovery_code: value } : { code: value }),
        });
        toast.success("Two-step verification turned off");
        reset();
        onChanged();
      } else {
        const result = await authService.mfaRegenerateRecoveryCodes({ password, code: value });
        reset();
        onNewCodes(result.recovery_codes);
      }
    } catch (cause) {
      setError(asApiError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <p className="text-supporting">
        {remaining} unused recovery {remaining === 1 ? "code" : "codes"} remaining.
        {remaining <= 2 ? " Generate a new set soon." : ""}
      </p>
      {mode === "idle" ? (
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => setMode("regenerate")}>
            New recovery codes
          </Button>
          <Button variant="outline" size="sm" onClick={() => setMode("disable")}>
            Turn off
          </Button>
        </div>
      ) : (
        <form onSubmit={(e) => void submit(e)} className="flex max-w-sm flex-col gap-3">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mfa-manage-password">Current password</Label>
            <Input
              id="mfa-manage-password"
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="mfa-manage-code">{useRecovery ? "Recovery code" : "Authentication code"}</Label>
            <Input
              id="mfa-manage-code"
              autoComplete="one-time-code"
              required
              className="font-mono tracking-widest"
              value={code}
              onChange={(e) => setCode(e.target.value)}
            />
            {mode === "disable" ? (
              <button
                type="button"
                className="text-caption text-primary w-fit hover:underline"
                onClick={() => {
                  setUseRecovery((v) => !v);
                  setCode("");
                }}
              >
                {useRecovery ? "Use authenticator code" : "Lost your phone? Use a recovery code"}
              </button>
            ) : null}
          </div>
          {error ? <ErrorState error={error} inline /> : null}
          <div className="flex gap-2">
            <LoadingButton
              type="submit"
              loading={busy}
              variant={mode === "disable" ? "destructive" : "default"}
            >
              {mode === "disable" ? "Turn off two-step verification" : "Generate new codes"}
            </LoadingButton>
            <Button type="button" variant="ghost" onClick={reset}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}

export function SecuritySettings() {
  const status = useApiQuery(() => authService.mfaStatus(), []);
  const [freshCodes, setFreshCodes] = useState<string[] | null>(null);

  return (
    <Panel
      title="Two-step verification"
      description="Require a code from an authenticator app in addition to your password."
      actions={
        status.data ? (
          <StatusBadge tone={status.data.enabled ? "success" : "neutral"}>
            {status.data.enabled ? "on" : "off"}
          </StatusBadge>
        ) : undefined
      }
      bodyClassName="p-4"
    >
      {status.loading ? (
        <p className="text-supporting">Loading…</p>
      ) : status.error ? (
        <ErrorState error={status.error} inline />
      ) : freshCodes ? (
        <RecoveryCodes
          codes={freshCodes}
          onDone={() => {
            setFreshCodes(null);
            status.reload();
          }}
        />
      ) : status.data?.enabled ? (
        <Manage
          remaining={status.data.recovery_codes_remaining}
          onChanged={status.reload}
          onNewCodes={setFreshCodes}
        />
      ) : (
        <div className="flex flex-col gap-3">
          {status.data?.required_for_staff ? (
            <p className="text-supporting flex items-center gap-2">
              <ShieldCheckIcon aria-hidden className="text-warning size-4" />
              Platform operator accounts are required to turn this on.
            </p>
          ) : null}
          <Enrol onEnabled={setFreshCodes} />
        </div>
      )}
    </Panel>
  );
}
