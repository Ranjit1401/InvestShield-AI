import { useMemo } from "react";
import { useParams } from "react-router-dom";
import { ArrowLeft, FileSearch } from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { ButtonLink } from "@/components/ui/button";
import { CardSkeletonList, ErrorState, EmptyState } from "@/components/common/state-blocks";
import { InvestigationHeader } from "@/components/investigation/investigation-header";
import { ClaimsSection } from "@/components/investigation/claims-section";
import { EntitiesSection } from "@/components/investigation/entities-section";
import { RedFlagsSection } from "@/components/investigation/red-flags-section";
import { TimelineSection } from "@/components/investigation/timeline-section";
import {
  DisclaimerPanel,
  LimitationsPanel,
} from "@/components/investigation/limitations-panel";
import { EvidenceSection } from "@/components/evidence/evidence-section";
import { RiskPanel } from "@/components/risk/risk-panel";
import { useInvestigation } from "@/hooks/use-investigations";

/** A section-level skeleton so a slow load never collapses the page. */
function ReportSkeleton() {
  return (
    <div className="space-y-6">
      <div className="space-y-3">
        <div className="h-7 w-56 animate-pulse rounded bg-surface-overlay" />
        <div className="h-5 w-80 animate-pulse rounded bg-surface-overlay" />
      </div>
      <CardSkeletonList rows={4} label="Loading investigation report" />
    </div>
  );
}

export function InvestigationResultPage() {
  const params = useParams<{ id: string }>();
  const investigationId = params.id ?? "";
  const { data, error, isInitialLoading, isLoading, reload } = useInvestigation(investigationId);

  /** Claim ids that actually have an evidence group, for the claims panel. */
  const evidenceClaimIds = useMemo(() => {
    const ids = new Set<string>();
    for (const group of data?.evidence ?? []) {
      if (group.evidence.length > 0) ids.add(group.claim_id);
    }
    return ids;
  }, [data]);

  if (isInitialLoading) {
    return (
      <PageContainer>
        <ReportSkeleton />
      </PageContainer>
    );
  }

  if (error) {
    return (
      <PageContainer>
        <div className="mx-auto max-w-3xl space-y-4">
          <ButtonLink to="/history" variant="ghost" size="sm">
            <ArrowLeft aria-hidden="true" />
            Back to history
          </ButtonLink>
          <ErrorState error={error} onRetry={reload}>
            {error.isNotFound ? (
              <p className="mt-2">
                Investigations are stored per environment. An id from another database will not be
                found here.
              </p>
            ) : null}
          </ErrorState>
        </div>
      </PageContainer>
    );
  }

  if (!data) {
    return (
      <PageContainer>
        <EmptyState
          icon={<FileSearch aria-hidden="true" className="size-7" />}
          title="No investigation to display"
          body="The investigation could not be loaded."
          action={
            <ButtonLink to="/history" variant="outline" size="sm">
              Back to history
            </ButtonLink>
          }
        />
      </PageContainer>
    );
  }

  return (
    <PageContainer>
      <div className="space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <ButtonLink to="/history" variant="ghost" size="sm">
            <ArrowLeft aria-hidden="true" />
            All investigations
          </ButtonLink>
          {isLoading ? (
            <span className="text-xs text-ink-faint" role="status">
              Refreshing…
            </span>
          ) : null}
        </div>

        <InvestigationHeader investigation={data} />

        <LimitationsPanel
          warnings={data.warnings}
          limitations={data.limitations}
          errors={data.errors}
        />

        <RiskPanel assessment={data.risk_assessment} />

        <RedFlagsSection redFlags={data.red_flags} />

        <ClaimsSection
          claims={data.claims}
          verificationResults={data.verification_results}
          evidenceClaimIds={evidenceClaimIds}
        />

        <EvidenceSection evidence={data.evidence} claims={data.claims} />

        <EntitiesSection entities={data.entities} />

        <TimelineSection timeline={data.timeline} />

        <DisclaimerPanel />
      </div>
    </PageContainer>
  );
}
