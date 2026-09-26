import { Suspense } from "react";

import { VerifyEmailView } from "@/features/auth/verify-email-view";

export default function VerifyEmailPage() {
  return (
    <Suspense>
      <VerifyEmailView />
    </Suspense>
  );
}
