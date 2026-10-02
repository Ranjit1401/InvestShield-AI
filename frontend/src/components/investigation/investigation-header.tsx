import { IdChip, InvestigationStatusBadge, MetaStat } from "@/components/common/badges";
import { Badge } from "@/components/ui/badge";
import { formatDateTime, humanizeEnum, inputTypeLabel } from "@/lib/format";
import type { InvestigationResponse } from "@/types/api";

/** The report header: what was investigated, when, and how it ended. */
export function InvestigationHeader({ investigation }: { investigation: InvestigationResponse }) {
  return (
    <header className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight text-ink sm:text-2xl">
            Investigation report
          </h1>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <IdChip value={investigation.investigation_id} label="id" />
            <InvestigationStatusBadge status={investigation.status} />
            <Badge tone="info">{inputTypeLabel(investigation.input_type)} input</Badge>
          </div>
        </div>
      </div>

      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 rounded-[var(--radius-card)] border border-hairline bg-surface-raised p-4 sm:grid-cols-4 sm:p-5">
        <MetaStat label="Investigation" value={investigation.investigation_id} />
        <MetaStat label="Started" value={formatDateTime(investigation.started_at)} />
        <MetaStat
          label="Completed"
          value={
            investigation.completed_at
              ? formatDateTime(investigation.completed_at)
              : "Not recorded by the API"
          }
        />
        <MetaStat
          label="Current stage"
          value={humanizeEnum(investigation.current_stage)}
        />
      </dl>
    </header>
  );
}
