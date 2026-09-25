"use client";

import { useParams, useSearchParams } from "next/navigation";
import { Suspense } from "react";

import { AcceptInvitationView } from "@/features/auth/accept-invitation-view";

function AcceptInvitationPageInner() {
  const params = useParams<{ id: string }>();
  const searchParams = useSearchParams();
  return <AcceptInvitationView id={params.id} token={searchParams.get("token")} />;
}

export default function AcceptInvitationPage() {
  return (
    <Suspense>
      <AcceptInvitationPageInner />
    </Suspense>
  );
}
