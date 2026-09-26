"use client";

import { CheckCircle2Icon } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { LogoMark } from "@/components/brand/logo";
import { ErrorState } from "@/components/ui/states";
import { Spinner } from "@/components/ui/spinner";
import { ApiError } from "@/lib/api/errors";
import { authService } from "@/services/auth";

export function VerifyEmailView() {
  const searchParams = useSearchParams();
  const uid = searchParams.get("uid");
  const token = searchParams.get("token");
  const [state, setState] = useState<"pending" | "done" | "error">("pending");
  const [error, setError] = useState<ApiError | null>(null);
  const missingToken = !uid || !token;

  useEffect(() => {
    if (missingToken) return;
    authService
      .confirmEmailVerification({ uid, token })
      .then(() => setState("done"))
      .catch((cause) => {
        setError(cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause)));
        setState("error");
      });
  }, [uid, token, missingToken]);

  const displayError = missingToken
    ? new ApiError(
        400,
        "MISSING_TOKEN",
        "This link is incomplete — copy it exactly as it was emailed to you.",
      )
    : error;
  const displayState = missingToken ? "error" : state;

  return (
    <div className="bg-background flex min-h-dvh items-center justify-center p-6">
      <div className="flex w-full max-w-sm flex-col items-center gap-4 text-center">
        <LogoMark className="size-10" />
        {displayState === "pending" ? (
          <>
            <Spinner className="size-6" />
            <p className="text-supporting">Confirming your email…</p>
          </>
        ) : null}
        {displayState === "done" ? (
          <>
            <span className="bg-success-soft text-success flex size-10 items-center justify-center rounded-full">
              <CheckCircle2Icon aria-hidden className="size-5" />
            </span>
            <p className="text-page-title">Email verified</p>
            <Link href="/" className="text-primary hover:underline">
              Continue to your workspace
            </Link>
          </>
        ) : null}
        {displayState === "error" && displayError ? <ErrorState error={displayError} /> : null}
      </div>
    </div>
  );
}
