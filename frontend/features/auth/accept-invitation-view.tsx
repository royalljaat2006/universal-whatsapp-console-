"use client";

import { EyeIcon, EyeOffIcon, LockIcon, MailIcon, UserIcon } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { LogoMark } from "@/components/brand/logo";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { LoadingButton } from "@/components/ui/spinner";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";
import { invitationService, type InvitationPreview } from "@/services/invitations";
import type { User } from "@/types/api";

export function AcceptInvitationView({ id, token }: { id: string; token: string | null }) {
  const router = useRouter();
  const [preview, setPreview] = useState<InvitationPreview | null>(null);
  const [previewError, setPreviewError] = useState<ApiError | null>(null);
  const [me, setMe] = useState<User | null>(null);
  const [meChecked, setMeChecked] = useState(false);
  const [fullName, setFullName] = useState("");
  const [password, setPassword] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<ApiError | null>(null);

  useEffect(() => {
    invitationService
      .preview(id)
      .then(setPreview)
      .catch((cause) => setPreviewError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause))));
    authService
      .me()
      .then(setMe)
      .catch(() => setMe(null))
      .finally(() => setMeChecked(true));
  }, [id]);

  async function onSubmit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await invitationService.accept(id, {
        token,
        password: me ? undefined : password,
        full_name: me ? undefined : fullName,
      });
      router.replace("/");
    } catch (cause) {
      setSubmitError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <Centered>
        <ErrorState
          error={new ApiError(400, "MISSING_TOKEN", "This link is missing its access token — copy the full link exactly as it was shared with you.")}
        />
      </Centered>
    );
  }

  if (previewError) {
    return (
      <Centered>
        <ErrorState error={previewError} />
      </Centered>
    );
  }

  if (!preview || !meChecked) {
    return (
      <Centered>
        <PageSkeleton />
      </Centered>
    );
  }

  if (preview.status !== "pending") {
    return (
      <Centered>
        <ErrorState
          error={new ApiError(
            403,
            "INVITATION_INVALID",
            preview.status === "accepted"
              ? "This invitation has already been accepted."
              : "This invitation has been revoked.",
          )}
        />
      </Centered>
    );
  }

  const emailMismatch = me !== null && me.email.toLowerCase() !== preview.email.toLowerCase();

  return (
    <Centered>
      <div className="flex flex-col items-center gap-2 text-center">
        <LogoMark className="size-10" />
        <h1 className="text-page-title">Join {preview.organization_name}</h1>
        <p className="text-supporting">
          You&rsquo;ve been invited as <span className="font-medium">{preview.role_name}</span> for{" "}
          <span className="font-mono">{preview.email}</span>.
        </p>
      </div>

      {emailMismatch ? (
        <ErrorState
          error={
            new ApiError(
              403,
              "EMAIL_MISMATCH",
              `You're signed in as ${me?.email}, but this invitation is for ${preview.email}. Log out and try again, or ask for a new invitation.`,
            )
          }
        />
      ) : (
        <form onSubmit={(e) => void onSubmit(e)} className="flex w-full max-w-sm flex-col gap-4">
          {me ? (
            <p className="text-supporting text-center">
              Signed in as <span className="font-medium">{me.email}</span> — accept below to join.
            </p>
          ) : (
            <>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="full-name">Your name</Label>
                <div className="relative">
                  <UserIcon aria-hidden className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                  <Input
                    id="full-name"
                    autoComplete="name"
                    required
                    className="h-10 pl-9"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="invite-email">Email</Label>
                <div className="relative">
                  <MailIcon aria-hidden className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                  <Input id="invite-email" value={preview.email} disabled className="h-10 pl-9" />
                </div>
              </div>
              <div className="flex flex-col gap-1.5">
                <Label htmlFor="password">Choose a password</Label>
                <div className="relative">
                  <LockIcon aria-hidden className="text-muted-foreground pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2" />
                  <Input
                    id="password"
                    type={showPassword ? "text" : "password"}
                    autoComplete="new-password"
                    required
                    minLength={10}
                    className="h-10 pr-10 pl-9"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((v) => !v)}
                    aria-label={showPassword ? "Hide password" : "Show password"}
                    className="text-muted-foreground hover:text-foreground absolute top-1/2 right-2 -translate-y-1/2 rounded p-1 outline-none"
                  >
                    {showPassword ? <EyeOffIcon aria-hidden className="size-4" /> : <EyeIcon aria-hidden className="size-4" />}
                  </button>
                </div>
              </div>
            </>
          )}
          {submitError ? <ErrorState error={submitError} inline /> : null}
          <LoadingButton type="submit" size="lg" className="h-10 w-full" loading={submitting}>
            {me ? "Accept invitation" : "Create account & join"}
          </LoadingButton>
        </form>
      )}
    </Centered>
  );
}

function Centered({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-background flex min-h-dvh items-center justify-center p-6">
      <div className="flex w-full max-w-md flex-col items-center gap-6">{children}</div>
    </div>
  );
}
