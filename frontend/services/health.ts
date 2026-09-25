import { ApiError } from "@/lib/api/errors";
import type { HealthReport } from "@/types/api";

/** Readiness returns 503 with a valid report body when unhealthy, so the
 * body is read regardless of status. */
export async function fetchReadiness(signal?: AbortSignal): Promise<HealthReport> {
  let response: Response;
  try {
    response = await fetch("/health/ready", { cache: "no-store", signal });
  } catch {
    throw new ApiError(0, "NETWORK_ERROR", "Backend unreachable.");
  }
  try {
    return (await response.json()) as HealthReport;
  } catch {
    throw new ApiError(response.status, "UPSTREAM_UNAVAILABLE", "Backend returned no health report.");
  }
}
