"use client";

import {
  CheckCircle2Icon,
  CircleDashedIcon,
  CircleHelpIcon,
  MinusCircleIcon,
  OctagonAlertIcon,
  RefreshCwIcon,
} from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { PageHeader, Panel } from "@/components/ui/page";
import { StatusBadge } from "@/components/ui/status-badge";
import { LoadingButton } from "@/components/ui/spinner";
import { ErrorState } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { ApiError } from "@/lib/api/errors";
import { toApiError } from "@/lib/format";
import { cn } from "@/lib/utils";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";
import { useUiMode } from "@/stores/ui-mode";
import type { SetupState, SetupStatus, SetupStep } from "@/types/api";

import { EmbeddedSignupButton } from "../numbers/embedded-signup-button";
import {
  OVERALL_LABEL,
  OVERALL_TONE,
  STATE_LABEL,
  STATE_TONE,
  currentStep,
  evidenceRows,
  fixWhere,
  progressOf,
} from "./model";
import { TestMessagePanel } from "./test-message-panel";

const ICON = {
  verified: CheckCircle2Icon,
  pending: CircleDashedIcon,
  failed: OctagonAlertIcon,
  unsupported: MinusCircleIcon,
  unknown: CircleHelpIcon,
} satisfies Record<SetupState, unknown>;

const COLOUR: Record<SetupState, string> = {
  verified: "text-success",
  pending: "text-info",
  failed: "text-destructive",
  unsupported: "text-muted-foreground",
  unknown: "text-muted-foreground",
};

function StepCard({
  step,
  index,
  advanced,
  highlight,
  children,
}: {
  step: SetupStep;
  index: number;
  advanced: boolean;
  highlight: boolean;
  children?: React.ReactNode;
}) {
  const Icon = ICON[step.state];
  const where = fixWhere(step);
  const rows = advanced ? evidenceRows(step.evidence) : [];
  return (
    <li
      aria-current={highlight ? "step" : undefined}
      className={cn("flex gap-3 px-4 py-4", highlight && "bg-brand-soft/30")}
    >
      <Icon aria-hidden className={cn("mt-0.5 size-5 shrink-0", COLOUR[step.state])} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-medium">
            <span className="text-muted-foreground">Step {index + 1}. </span>
            {step.title}
          </h3>
          <StatusBadge tone={STATE_TONE[step.state]}>{STATE_LABEL[step.state]}</StatusBadge>
        </div>
        <p className="text-supporting mt-1">{step.detail}</p>
        {step.next_step && step.state !== "verified" ? (
          <p className="mt-1 text-sm">
            <span className="font-medium">What to do: </span>
            {step.next_step}
          </p>
        ) : null}
        {where ? <p className="text-caption mt-1">{where}</p> : null}
        {rows.length ? (
          <details className="mt-2">
            <summary className="text-supporting cursor-pointer text-sm">What this is based on</summary>
            <dl className="mt-1 grid grid-cols-[12rem_1fr] gap-x-3 gap-y-1 text-sm">
              {rows.map((row) => (
                <div key={row.label} className="contents">
                  <dt className="text-muted-foreground">{row.label}</dt>
                  <dd className="break-words">{row.value}</dd>
                </div>
              ))}
            </dl>
          </details>
        ) : null}
        {children ? <div className="mt-3">{children}</div> : null}
      </div>
    </li>
  );
}

export function SetupView() {
  const { business, hasPermission } = useSession();
  const { mode } = useUiMode();
  const toast = useToast();
  const [data, setData] = useState<SetupStatus | undefined>();
  const [error, setError] = useState<ApiError | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const alive = useRef(true);
  const canManage = hasPermission("numbers.manage");
  const canSend = hasPermission("messages.send");

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const load = useCallback(async () => {
    try {
      const fresh = await whatsappService.setup();
      if (!alive.current) return undefined;
      setData(fresh);
      setError(null);
      return fresh;
    } catch (cause) {
      if (alive.current) setError(toApiError(cause));
      return undefined;
    } finally {
      if (alive.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- initial fetch on mount / business change
    void load();
  }, [load, business?.id]);

  /** Ask Meta again (verify + sync), then wait until the "last checked" time really moved. */
  async function refresh() {
    if (!data || data.accounts.length === 0) return;
    setRefreshing(true);
    const before = String(data.steps.find((s) => s.id === "connection")?.evidence.last_verified_at ?? "");
    try {
      await Promise.all(
        data.accounts.flatMap((a) => [whatsappService.requestVerify(a.id), whatsappService.requestSync(a.id)]),
      );
      for (let attempt = 0; attempt < 10; attempt++) {
        await new Promise((resolve) => setTimeout(resolve, 2500));
        if (!alive.current) return;
        const fresh = await load();
        const after = String(fresh?.steps.find((s) => s.id === "connection")?.evidence.last_verified_at ?? "");
        if (fresh && after && after !== before) {
          toast.success("Updated from Meta", "The status below is what Meta reported just now.");
          return;
        }
      }
      toast.info("Still waiting for Meta", "The refresh was requested but Meta has not answered yet.");
    } catch (cause) {
      toast.error("Couldn't refresh", toApiError(cause).message);
    } finally {
      if (alive.current) setRefreshing(false);
    }
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader title="Set up WhatsApp" />
        <p className="text-supporting" role="status">
          Checking your setup…
        </p>
      </div>
    );
  }
  if (error) {
    return (
      <div className="flex flex-col gap-4">
        <PageHeader title="Set up WhatsApp" />
        <ErrorState error={error} onRetry={() => void load()} />
      </div>
    );
  }
  if (!data) return null;

  const { done, total } = progressOf(data.steps);
  const current = currentStep(data.steps, data.next_step);
  const advanced = mode === "advanced";
  const guided = data.capabilities.embedded_signup;

  return (
    <div className="flex flex-col gap-4">
      <PageHeader
        title="Set up WhatsApp"
        description="Connect your account, confirm it works, and send one verified test message."
        actions={
          data.accounts.length ? (
            <LoadingButton size="sm" variant="outline" loading={refreshing} onClick={() => void refresh()}>
              <RefreshCwIcon aria-hidden /> Refresh from Meta
            </LoadingButton>
          ) : null
        }
      />

      <section
        aria-label="Setup progress"
        className="bg-surface flex flex-wrap items-center justify-between gap-2 rounded-lg border px-4 py-3 shadow-xs"
      >
        <div>
          <p className="text-sm font-medium">{data.summary}</p>
          <p className="text-supporting">
            {done} of {total} steps done. Your progress is saved automatically: it comes from what is
            really set up, so you can leave and come back.
          </p>
        </div>
        <StatusBadge tone={OVERALL_TONE[data.overall]}>{OVERALL_LABEL[data.overall]}</StatusBadge>
      </section>

      {data.server.state !== "verified" ? (
        <Panel title="Needs your administrator">
          <ul className="divide-y">
            <StepCard step={data.server} index={-1} advanced={advanced} highlight={false} />
          </ul>
        </Panel>
      ) : null}

      <Panel title="Steps">
        <ol className="divide-y">
          {data.steps.map((step, index) => (
            <StepCard
              key={step.id}
              step={step}
              index={index}
              advanced={advanced}
              highlight={current?.id === step.id && data.overall !== "ready"}
            >
              {step.id === "connection" && step.state !== "verified" ? (
                guided.state === "verified" ? (
                  canManage && business ? (
                    <EmbeddedSignupButton businessId={business.id} onConnected={() => void load()} />
                  ) : (
                    <p className="text-supporting">
                      {business
                        ? "Only someone who can manage WhatsApp numbers can connect an account. Ask an administrator."
                        : "Choose a business first."}
                    </p>
                  )
                ) : (
                  <p className="text-supporting">{guided.detail}</p>
                )
              ) : null}
              {step.id === "webhook_delivery" ? (
                <p className="text-caption">
                  {data.capabilities.callback_check.detail}
                  {advanced ? ` Callback address: ${data.webhook.callback_url}` : ""}
                </p>
              ) : null}
              {step.id === "test_message" ? (
                <TestMessagePanel setup={data} canSend={canSend} onChanged={() => void load()} />
              ) : null}
            </StepCard>
          ))}
        </ol>
      </Panel>
    </div>
  );
}
