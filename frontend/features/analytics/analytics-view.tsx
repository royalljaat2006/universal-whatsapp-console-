"use client";

import { BarChart3Icon } from "lucide-react";
import { useState } from "react";

import { BarList } from "@/components/charts/bar-list";
import { LineChart } from "@/components/charts/line-chart";
import { ApiErrorNotice } from "@/components/status/api-error-notice";
import { CapabilityStatus } from "@/components/status/capability-status";
import { Badge } from "@/components/ui/badge";
import { Kpi, Panel } from "@/components/ui/page";
import { EmptyState } from "@/components/ui/states";
import { Input } from "@/components/ui/input";
import { NativeSelect } from "@/components/ui/native-select";
import { ExportButton } from "@/features/reports/export-button";
import { useApiQuery } from "@/hooks/use-api-query";
import { count, money, NO_DATA, percent, presetRange, type RangePreset } from "@/lib/analytics";
import { cn } from "@/lib/utils";
import { type AnalyticsFilters, analyticsService } from "@/services/analytics";
import { whatsappService } from "@/services/whatsapp";
import { useSession } from "@/stores/session-context";
import type { AnalyticsOverview, CostDimension } from "@/types/api";

const PRESETS: { id: RangePreset; label: string }[] = [
  { id: "today", label: "Today" },
  { id: "yesterday", label: "Yesterday" },
  { id: "7d", label: "Last 7 days" },
  { id: "30d", label: "Last 30 days" },
  { id: "custom", label: "Custom" },
];

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return <Kpi label={label} value={value} hint={hint} />;
}

function Section({
  title,
  children,
  action,
}: {
  title: string;
  children: React.ReactNode;
  action?: React.ReactNode;
}) {
  return (
    <Panel title={title} actions={action} bodyClassName="p-4">
      {children}
    </Panel>
  );
}

function costHint(o: AnalyticsOverview): string {
  const c = o.cost;
  if (c.state === "none") return "No billable messages priced in this range";
  if (c.state === "pending_rate") return `${c.pending_rate_messages} billable message(s) await a rate card`;
  if (c.state === "partial") return `${c.pending_rate_messages} billable message(s) not yet priced`;
  return `${c.free_messages} free message(s)`;
}

/**
 * Business analytics from backend rollups (never computed in the browser,
 * never sample data). Empty ranges say "No data available" instead of 0 %.
 */
export function AnalyticsView() {
  const { business } = useSession();
  const tz = business?.timezone || undefined;
  const [preset, setPreset] = useState<RangePreset>("7d");
  const [custom, setCustom] = useState(() => presetRange("7d", tz));
  const [numberId, setNumberId] = useState("");
  const [costDimension, setCostDimension] = useState<CostDimension>("date");

  const range = preset === "custom" ? custom : presetRange(preset, tz);
  const filters: AnalyticsFilters = { ...range, phone_number: numberId || undefined };
  const key = [business?.id, filters];
  const enabled = Boolean(business);

  const numbersList = useApiQuery(() => whatsappService.numbers(), [business?.id], enabled);
  const overview = useApiQuery(() => analyticsService.overview(filters), key, enabled);
  const series = useApiQuery(() => analyticsService.timeseries(filters), key, enabled);
  const numbers = useApiQuery(() => analyticsService.numbers(filters), key, enabled);
  const templates = useApiQuery(() => analyticsService.templates(filters), key, enabled);
  const failures = useApiQuery(() => analyticsService.failures(filters), key, enabled);
  const costs = useApiQuery(
    () => analyticsService.costs(filters, costDimension),
    [...key, costDimension],
    enabled,
  );

  if (!business) {
    return (
      <CapabilityStatus
        title="Analytics"
        state="CONFIGURATION REQUIRED"
        description="Select a business first."
      />
    );
  }

  const o = overview.data;
  const error =
    overview.error || series.error || numbers.error || templates.error || failures.error || costs.error;
  const labels =
    series.data?.points.map((p) =>
      series.data?.granularity === "hour"
        ? new Date(p.bucket).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
        : p.bucket.slice(5),
    ) ?? [];
  const points = series.data?.points ?? [];

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-page-title">Analytics</h1>
          <p className="text-caption mt-0.5">
            {o ? `${o.range.date_from} → ${o.range.date_to} (${o.range.timezone})` : "Loading…"}
            {o?.freshness.computed_at
              ? ` · aggregated ${new Date(o.freshness.computed_at).toLocaleString()}`
              : ""}
            {o && o.freshness.pending_buckets > 0
              ? ` · ${o.freshness.pending_buckets} update(s) pending`
              : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <NativeSelect aria-label="Number" value={numberId} onChange={(e) => setNumberId(e.target.value)}>
            <option value="">All numbers</option>
            {numbersList.data?.results.map((n) => (
              <option key={n.id} value={n.id}>
                {n.display_phone_number}
              </option>
            ))}
          </NativeSelect>
          <div role="group" aria-label="Date range" className="flex rounded-md border">
            {PRESETS.map((p) => (
              <button
                key={p.id}
                type="button"
                aria-pressed={preset === p.id}
                onClick={() => setPreset(p.id)}
                className={cn(
                  "px-2 py-1 text-xs",
                  preset === p.id ? "bg-muted font-medium" : "hover:bg-muted/60",
                )}
              >
                {p.label}
              </button>
            ))}
          </div>
          {preset === "custom" ? (
            <span className="flex items-center gap-1 text-xs">
              <Input
                type="date"
                aria-label="From"
                className="h-8 w-36"
                value={custom.date_from}
                max={custom.date_to}
                onChange={(e) => setCustom((c) => ({ ...c, date_from: e.target.value }))}
              />
              <Input
                type="date"
                aria-label="To"
                className="h-8 w-36"
                value={custom.date_to}
                min={custom.date_from}
                onChange={(e) => setCustom((c) => ({ ...c, date_to: e.target.value }))}
              />
            </span>
          ) : null}
          <ExportButton kind="analytics_daily" params={range} />
        </div>
      </div>

      {error ? <ApiErrorNotice error={error} /> : null}

      {o && !o.has_data ? (
        <Panel>
          <EmptyState
            icon={BarChart3Icon}
            title={`${NO_DATA} for this range`}
            description="Metrics appear once messages are sent or received through this business's numbers."
          />
        </Panel>
      ) : null}

      {o?.has_data ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 xl:grid-cols-7">
          <Stat
            label="Messages"
            value={count(o.totals.inbound + o.totals.outbound)}
            hint={`${count(o.totals.outbound)} out · ${count(o.totals.inbound)} in`}
          />
          <Stat
            label="Delivery rate"
            value={percent(o.rates.delivery_rate)}
            hint={`${count(o.totals.delivered)} delivered`}
          />
          <Stat label="Read rate" value={percent(o.rates.read_rate)} hint={`${count(o.totals.read)} read`} />
          <Stat
            label="Failures"
            value={count(o.totals.failed)}
            hint={o.rates.failure_rate === null ? NO_DATA : `${percent(o.rates.failure_rate)} of settled`}
          />
          <Stat
            label="Conversations"
            value={count(o.active_conversations)}
            hint={
              o.resolved_conversations === null
                ? "resolved: n/a for this filter"
                : `${count(o.resolved_conversations)} resolved`
            }
          />
          <Stat label="Unique contacts" value={count(o.unique_contacts)} />
          <Stat
            label="Cost"
            value={
              o.cost.by_currency.length
                ? money(o.cost.by_currency)
                : o.cost.state === "pending_rate"
                  ? "Pending"
                  : "—"
            }
            hint={costHint(o)}
          />
        </div>
      ) : null}

      {o?.has_data ? (
        <>
          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Message volume (inbound vs outbound)">
              <LineChart
                ariaLabel="Inbound and outbound messages per bucket"
                labels={labels}
                series={[
                  {
                    name: "Outbound",
                    color: "var(--chart-1, #2563eb)",
                    values: points.map((p) => p.outbound),
                  },
                  { name: "Inbound", color: "var(--chart-2, #16a34a)", values: points.map((p) => p.inbound) },
                ]}
              />
            </Section>
            <Section title="Delivery / read / failure">
              <LineChart
                ariaLabel="Delivered, read and failed outbound messages per bucket"
                labels={labels}
                series={[
                  {
                    name: "Delivered",
                    color: "var(--chart-3, #0891b2)",
                    values: points.map((p) => p.delivered),
                  },
                  { name: "Read", color: "var(--chart-4, #7c3aed)", values: points.map((p) => p.read) },
                  { name: "Failed", color: "var(--chart-5, #dc2626)", values: points.map((p) => p.failed) },
                ]}
              />
            </Section>
            <Section title="Cost over time">
              {series.data && series.data.cost.length ? (
                <BarList
                  ariaLabel="Cost per day"
                  items={series.data.cost.map((c) => ({
                    key: `${c.date}-${c.currency}`,
                    label: `${c.date} (${c.currency})`,
                    value: Number(c.amount),
                  }))}
                  valueLabel={(v) => v.toLocaleString(undefined, { maximumFractionDigits: 4 })}
                />
              ) : (
                <p className="text-muted-foreground text-sm">
                  {series.data?.granularity === "hour"
                    ? "Costs are aggregated per day; choose a range of 2+ days."
                    : "Cost data unavailable: no priced messages (load a rate card to price billable messages)."}
                </p>
              )}
            </Section>
            <Section title="Template usage">
              {templates.data && templates.data.templates.length ? (
                <BarList
                  ariaLabel="Template sends"
                  items={templates.data.templates.map((t) => ({
                    key: t.template_id,
                    label: `${t.name} (${t.language})`,
                    value: t.sends,
                    hint: `${t.unique_recipients} recipients · delivered ${percent(t.rates.delivery_rate)} · read ${percent(t.rates.read_rate)}`,
                  }))}
                />
              ) : (
                <p className="text-muted-foreground text-sm">No template sends in this range.</p>
              )}
            </Section>
          </div>

          <Section title="Number performance">
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-muted-foreground text-left text-xs">
                  <tr>
                    <th className="py-1 pr-3">Number</th>
                    <th className="py-1 pr-3">Out</th>
                    <th className="py-1 pr-3">In</th>
                    <th className="py-1 pr-3">Delivered</th>
                    <th className="py-1 pr-3">Read</th>
                    <th className="py-1 pr-3">Failed</th>
                    <th className="py-1 pr-3">Contacts</th>
                    <th className="py-1 pr-3">Templates</th>
                    <th className="py-1 pr-3">Cost</th>
                    <th className="py-1">Quality / limit</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {numbers.data?.numbers.map((n) => (
                    <tr key={n.phone_number_id}>
                      <td className="py-1.5 pr-3">
                        {n.display_phone_number}
                        <span className="text-muted-foreground block text-[11px]">
                          {n.label || n.verified_name}
                        </span>
                      </td>
                      {n.has_data ? (
                        <>
                          <td className="pr-3 tabular-nums">{count(n.totals.outbound)}</td>
                          <td className="pr-3 tabular-nums">{count(n.totals.inbound)}</td>
                          <td className="pr-3 tabular-nums">{percent(n.rates.delivery_rate)}</td>
                          <td className="pr-3 tabular-nums">{percent(n.rates.read_rate)}</td>
                          <td className="pr-3 tabular-nums">{count(n.totals.failed)}</td>
                          <td className="pr-3 tabular-nums">{count(n.unique_contacts)}</td>
                          <td className="pr-3 tabular-nums">{count(n.templates_used)}</td>
                          <td className="pr-3">{money(n.cost)}</td>
                        </>
                      ) : (
                        <td colSpan={8} className="text-muted-foreground pr-3 text-xs">
                          {NO_DATA}
                        </td>
                      )}
                      <td className="text-xs">
                        <Badge variant="outline">{n.quality_rating || "quality n/a"}</Badge>{" "}
                        <span className="text-muted-foreground">{n.messaging_limit_tier || "limit n/a"}</span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <div className="grid gap-4 lg:grid-cols-2">
            <Section title="Failures">
              {failures.data && failures.data.total ? (
                <div className="flex flex-col gap-3 text-sm">
                  <ul className="flex flex-col gap-1">
                    {failures.data.groups.map((g) => (
                      <li key={g.category} className="flex justify-between">
                        <span>
                          {g.label}
                          {g.retryable ? (
                            <Badge variant="outline" className="ml-1">
                              retryable
                            </Badge>
                          ) : null}
                        </span>
                        <span className="tabular-nums">{g.count}</span>
                      </li>
                    ))}
                  </ul>
                  <table className="w-full text-xs">
                    <tbody className="divide-y">
                      {failures.data.recent.slice(0, 10).map((f) => (
                        <tr key={f.message_id}>
                          <td className="py-1 pr-2">{new Date(f.occurred_at).toLocaleString()}</td>
                          <td className="pr-2">{f.contact}</td>
                          <td className="pr-2 font-mono">{f.error_code}</td>
                          <td>{f.error_message}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : (
                <p className="text-muted-foreground text-sm">No failed messages in this range.</p>
              )}
            </Section>
            <Section
              title="Cost breakdown"
              action={
                <NativeSelect
                  aria-label="Group cost by"
                  className="h-7 text-xs"
                  value={costDimension}
                  onChange={(e) => setCostDimension(e.target.value as CostDimension)}
                >
                  <option value="date">Date</option>
                  <option value="phone_number">Number</option>
                  <option value="business_account">WABA</option>
                  <option value="template">Template</option>
                  <option value="campaign">Campaign</option>
                  <option value="category">Category</option>
                </NativeSelect>
              }
            >
              {costs.data && costs.data.rows.length ? (
                <table className="w-full text-sm">
                  <tbody className="divide-y">
                    {costs.data.rows.map((r) => (
                      <tr key={`${r.key}-${r.currency}`}>
                        <td className="py-1 pr-2">{r.label}</td>
                        <td className="pr-2 text-xs">{r.messages} msg</td>
                        <td className="text-right tabular-nums">
                          {r.currency}{" "}
                          {Number(r.amount).toLocaleString(undefined, { maximumFractionDigits: 4 })}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : (
                <p className="text-muted-foreground text-sm">
                  Cost data unavailable
                  {o.cost.pending_rate_messages
                    ? ` — ${o.cost.pending_rate_messages} billable message(s) pending a rate card`
                    : ""}
                  .
                </p>
              )}
            </Section>
          </div>
        </>
      ) : null}
    </div>
  );
}
