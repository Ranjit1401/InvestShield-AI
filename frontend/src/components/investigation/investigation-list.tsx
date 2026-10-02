import { FileSearch } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ButtonLink } from "@/components/ui/button";
import { InvestigationStatusBadge } from "@/components/common/badges";
import { formatDateTime, humanizeEnum, inputTypeLabel } from "@/lib/format";
import { ChevronRight } from "lucide-react";
import type { InvestigationSummaryResponse } from "@/types/api";

/** Counts available on a list summary, labelled for display. */
function CountChip({ label, value }: { label: string; value: number | undefined }) {
  if (typeof value !== "number") return null;
  return (
    <span className="inline-flex items-center gap-1 text-xs text-ink-faint">
      <span className="font-mono text-ink-muted">{value}</span>
      {label}
    </span>
  );
}

export interface InvestigationListProps {
  investigations: InvestigationSummaryResponse[];
  /** Show the per-record counts. Off in compact contexts such as the dashboard. */
  showCounts?: boolean;
}

/**
 * The stored-investigation list.
 *
 * Risk level is intentionally absent: the summary endpoint does not return it,
 * so no risk badge is rendered here. Only the fields the API actually provides
 * are shown.
 */
export function InvestigationList({ investigations, showCounts = true }: InvestigationListProps) {
  return (
    <ul className="space-y-3">
      {investigations.map((item) => (
        <li key={`${item.investigation_id}-${item.created_at}`}>
          <Card className="transition-colors hover:border-accent-muted/60">
            <CardContent className="p-4 sm:p-5">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-ink">{item.investigation_id}</span>
                    <InvestigationStatusBadge status={item.status} />
                    <Badge tone="info">{inputTypeLabel(item.input_type)}</Badge>
                  </div>

                  <p className="mt-1.5 text-xs text-ink-faint">
                    Started {formatDateTime(item.started_at)} · recorded{" "}
                    {formatDateTime(item.created_at)} · stage{" "}
                    {humanizeEnum(item.current_stage)}
                  </p>

                  {showCounts ? (
                    <div className="mt-2.5 flex flex-wrap gap-x-3 gap-y-1.5">
                      <CountChip label="claims" value={item.claim_count} />
                      <CountChip label="entities" value={item.entity_count} />
                      <CountChip label="red flags" value={item.red_flag_count} />
                      <CountChip label="verified" value={item.verification_count} />
                      <CountChip label="evidence" value={item.evidence_count} />
                      <CountChip label="sources" value={item.source_count} />
                      <CountChip label="factors" value={item.factor_count} />
                    </div>
                  ) : null}
                </div>

                <div className="flex shrink-0 items-center gap-2">
                  <ButtonLink
                    to={`/investigation/${encodeURIComponent(item.investigation_id)}`}
                    size="sm"
                    variant="secondary"
                    aria-label={`Open investigation ${item.investigation_id}`}
                  >
                    Open
                    <ChevronRight aria-hidden="true" />
                  </ButtonLink>
                </div>
              </div>
            </CardContent>
          </Card>
        </li>
      ))}
    </ul>
  );
}

/** Placeholder shown when a filter yields nothing on a page that has data. */
export function NoResultsNotice() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-[var(--radius-card)] border border-dashed border-hairline px-6 py-10 text-center">
      <FileSearch aria-hidden="true" className="size-7 text-ink-faint" />
      <p className="text-sm font-medium text-ink">No investigations on this page</p>
      <p className="max-w-sm text-sm text-ink-muted">
        There are stored investigations, but none on this page of results. Go back a page to see
        earlier ones.
      </p>
    </div>
  );
}
