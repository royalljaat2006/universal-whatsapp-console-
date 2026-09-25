"use client";

import { ArrowLeftIcon } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { DetailList, PageHeader, Panel } from "@/components/ui/page";
import { LoadingButton } from "@/components/ui/spinner";
import { ErrorState, PageSkeleton } from "@/components/ui/states";
import { StatusBadge } from "@/components/ui/status-badge";
import { useToast } from "@/components/ui/toast";
import { useApiQuery } from "@/hooks/use-api-query";
import { ApiError } from "@/lib/api/errors";
import { platformService } from "@/services/platform";

export function OrganizationDetail({
  organizationId,
  onBack,
}: {
  organizationId: string;
  onBack: () => void;
}) {
  const org = useApiQuery(() => platformService.organizations.get(organizationId), [organizationId]);
  const [updating, setUpdating] = useState(false);
  const toast = useToast();

  async function toggleStatus() {
    if (!org.data) return;
    const next = org.data.status === "active" ? "suspended" : "active";
    const confirmed = window.confirm(
      next === "suspended"
        ? `Suspend "${org.data.name}"? Every member loses access immediately.`
        : `Reactivate "${org.data.name}"? Members regain access immediately.`,
    );
    if (!confirmed) return;
    setUpdating(true);
    try {
      await platformService.organizations.setStatus(organizationId, next);
      toast.success(next === "suspended" ? "Organization suspended" : "Organization reactivated");
      org.reload();
    } catch (cause) {
      const error = cause instanceof ApiError ? cause : new ApiError(0, "CLIENT_ERROR", String(cause));
      toast.error("Couldn't update status", error.message);
    } finally {
      setUpdating(false);
    }
  }

  if (org.error) return <ErrorState error={org.error} onRetry={org.reload} />;
  if (!org.data) return <PageSkeleton />;

  const isActive = org.data.status === "active";

  return (
    <div className="flex flex-col gap-5">
      <Button variant="ghost" size="sm" onClick={onBack} className="w-fit">
        <ArrowLeftIcon aria-hidden /> Organizations
      </Button>
      <PageHeader
        title={org.data.name}
        description={<span className="font-mono">{org.data.slug}</span>}
        actions={
          <LoadingButton
            variant={isActive ? "destructive" : "default"}
            loading={updating}
            onClick={() => void toggleStatus()}
          >
            {isActive ? "Suspend organization" : "Reactivate organization"}
          </LoadingButton>
        }
      />
      <Panel title="Overview" bodyClassName="p-4">
        <DetailList
          items={[
            {
              label: "Status",
              value: (
                <StatusBadge tone={isActive ? "success" : "danger"}>{org.data.status}</StatusBadge>
              ),
            },
            { label: "Time zone", value: org.data.timezone },
            { label: "Members", value: org.data.member_count },
            { label: "Businesses", value: org.data.business_count },
            { label: "Created", value: new Date(org.data.created_at).toLocaleString() },
          ]}
        />
      </Panel>
      <Panel title="Audit history" description="Actions recorded for this organization." bodyClassName="p-4">
        <Link
          href={`/platform/audit-logs?organization_id=${organizationId}`}
          className="text-primary text-sm hover:underline"
        >
          View this organization&rsquo;s audit log →
        </Link>
      </Panel>
    </div>
  );
}
