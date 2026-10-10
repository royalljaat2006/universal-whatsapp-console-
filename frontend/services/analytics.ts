import { api } from "@/lib/api/client";
import type {
  AnalyticsFailures,
  AnalyticsOverview,
  AnalyticsRange,
  AnalyticsTimeseries,
  CostBreakdown,
  CostDimension,
  NumberAnalytics,
  TemplateAnalytics,
} from "@/types/api";

export interface AnalyticsFilters {
  date_from: string;
  date_to: string;
  phone_number?: string;
  template?: string;
  campaign?: string;
  language?: string;
}

const q = (filters: AnalyticsFilters, extra: Record<string, string | undefined> = {}) => ({
  query: { ...filters, ...extra } as Record<string, string | undefined>,
});

/** Read-only analytics over backend rollups; business scope comes from the session header. */
export const analyticsService = {
  overview: (f: AnalyticsFilters, options: { businessId?: string } = {}) =>
    api.get<AnalyticsOverview>("/api/v1/analytics/overview/", { ...q(f), ...options }),
  timeseries: (f: AnalyticsFilters, granularity?: "hour" | "day") =>
    api.get<AnalyticsTimeseries>("/api/v1/analytics/timeseries/", q(f, { granularity })),
  numbers: (f: AnalyticsFilters) =>
    api.get<{ range: AnalyticsRange; numbers: NumberAnalytics[] }>("/api/v1/analytics/numbers/", q(f)),
  templates: (f: AnalyticsFilters) =>
    api.get<{ range: AnalyticsRange; templates: TemplateAnalytics[] }>("/api/v1/analytics/templates/", q(f)),
  costs: (f: AnalyticsFilters, groupBy: CostDimension) =>
    api.get<CostBreakdown>("/api/v1/analytics/costs/", q(f, { group_by: groupBy })),
  failures: (f: AnalyticsFilters) => api.get<AnalyticsFailures>("/api/v1/analytics/failures/", q(f)),
};
