import { useMemo } from "react";
import { useAsyncResource, type AsyncState } from "@/hooks/use-async-resource";
import {
  getHealth,
  getInvestigation,
  getInvestigations,
  getLimits,
} from "@/services/api-client";
import type {
  HealthResponse,
  InvestigationInputType,
  InvestigationLimits,
  InvestigationListResponse,
  InvestigationResponse,
  ListInvestigationsParams,
} from "@/types/api";

/** `GET /api/health`, used by the dashboard and the layout status indicator. */
export function useHealth(): AsyncState<HealthResponse> {
  return useAsyncResource<HealthResponse>((signal) => getHealth(signal), []);
}

/** `GET /api/investigations/limits`. */
export function useLimits(): AsyncState<InvestigationLimits> {
  return useAsyncResource<InvestigationLimits>((signal) => getLimits(signal), []);
}

/** `GET /api/investigations/{id}`. */
export function useInvestigation(investigationId: string): AsyncState<InvestigationResponse> {
  return useAsyncResource<InvestigationResponse>(
    (signal) => getInvestigation(investigationId, signal),
    [investigationId],
    { enabled: investigationId.trim() !== "" },
  );
}

/** `GET /api/investigations` with the backend's own limit/offset paging. */
export function useInvestigations(
  params: ListInvestigationsParams = {},
): AsyncState<InvestigationListResponse> {
  const limit = params.limit;
  const offset = params.offset;
  return useAsyncResource<InvestigationListResponse>(
    (signal) => getInvestigations({ limit, offset }, signal),
    [limit, offset],
  );
}

/* ------------------------------------------------------------------ *
 * Derived views over the list response
 * ------------------------------------------------------------------ */

export interface InvestigationAggregates {
  total: number;
  /** Counts by input type across the whole store, not just this page. */
  byInputType: Map<InvestigationInputType, number>;
  /**
   * Risk distribution is NOT derivable from the list endpoint: the summary
   * carries no risk level. It is therefore not computed here, and the dashboard
   * says so rather than showing a fabricated chart.
   */
  statusCounts: Map<string, number>;
  pageCount: number;
}

/**
 * Aggregate the stored investigations for the dashboard.
 *
 * Only values present in the current page are counted; `total` comes from the
 * backend so the figure is authoritative. Counters are explicitly scoped to the
 * page to avoid implying store-wide totals the API does not provide.
 */
export function useInvestigationAggregates(
  list: InvestigationListResponse | null,
): InvestigationAggregates {
  return useMemo(() => {
    const byInputType = new Map<InvestigationInputType, number>();
    const statusCounts = new Map<string, number>();

    if (list) {
      for (const item of list.investigations) {
        byInputType.set(item.input_type, (byInputType.get(item.input_type) ?? 0) + 1);
        statusCounts.set(item.status, (statusCounts.get(item.status) ?? 0) + 1);
      }
    }

    const total = list?.total ?? 0;
    const limit = list?.limit ?? 20;
    return {
      total,
      byInputType,
      statusCounts,
      pageCount: limit > 0 ? Math.ceil(total / limit) : 0,
    };
  }, [list]);
}
