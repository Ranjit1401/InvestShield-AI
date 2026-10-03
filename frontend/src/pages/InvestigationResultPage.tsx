import { useMemo, useState } from "react";
import { useParams } from "react-router-dom";
import { ArrowLeft, FileSearch, Globe } from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { ButtonLink } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
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
import { EvidenceGraph } from "@/components/evidence/evidence-graph";
import { RiskPanel } from "@/components/risk/risk-panel";
import { useInvestigation } from "@/hooks/use-investigations";
import type { Language } from "@/types/api";

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
  const [selectedLanguage, setSelectedLanguage] = useState<Language>("en");
  const { data, error, isInitialLoading, isLoading, reload } = useInvestigation(
    investigationId,
    selectedLanguage,
  );

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

  /** Localized section titles, with the backend's English as the fallback. */
  const sections = data.report?.sections ?? {};
  const title = (key: string, fallback: string) => sections[key] ?? fallback;

  return (
    <PageContainer>
      <div className="space-y-8">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <ButtonLink to="/history" variant="ghost" size="sm">
            <ArrowLeft aria-hidden="true" />
            All investigations
          </ButtonLink>
          <div className="flex items-center gap-3">
            <div className="flex items-center gap-2">
              <Globe className="size-4 text-ink-muted" aria-hidden="true" />
              <label htmlFor="report-language-select" className="text-xs font-medium text-ink-muted">
                Report Language:
              </label>
              <select
                id="report-language-select"
                value={selectedLanguage}
                onChange={(e) => setSelectedLanguage(e.target.value as Language)}
                className="h-8 rounded-md border border-hairline bg-surface px-2.5 text-xs font-medium text-ink focus:border-accent-muted focus:outline-none"
              >
                <option value="en">English (en)</option>
                <option value="hi">हिन्दी (hi)</option>
                <option value="mr">मराठी (mr)</option>
              </select>
            </div>
            {isLoading ? (
              <span className="text-xs text-ink-faint" role="status">
                Refreshing…
              </span>
            ) : null}
          </div>
        </div>

        <InvestigationHeader investigation={data} />

        {data.report?.summary ? (
          <Card className="border-accent/20 bg-accent/5 p-4">
            <div className="flex items-start gap-3">
              <Globe className="mt-0.5 size-5 shrink-0 text-accent" aria-hidden="true" />
              <div className="space-y-1">
                <h3 className="text-sm font-semibold text-ink">
                  {title("summary", "Summary")}
                </h3>
                <p className="text-sm leading-relaxed text-ink-muted">{data.report.summary}</p>
              </div>
            </div>
          </Card>
        ) : null}

        <LimitationsPanel
          warnings={data.warnings}
          limitations={data.limitations}
          errors={data.errors}
          title={title("limitations", "Limitations")}
        />

        <RiskPanel
          assessment={data.risk_assessment}
          title={title("risk_assessment", "Risk Assessment")}
        />

        <RedFlagsSection
          redFlags={data.red_flags}
          title={title("why_flagged", "Why This Was Flagged")}
          factors={data.risk_assessment?.factors ?? []}
        />

        <ClaimsSection
          claims={data.claims}
          verificationResults={data.verification_results}
          evidenceClaimIds={evidenceClaimIds}
          title={title("claims", "Claims")}
        />

        <EvidenceSection
          evidence={data.evidence}
          claims={data.claims}
          title={title("evidence", "Evidence")}
        />

        <EvidenceGraph
          claims={data.claims}
          entities={data.entities}
          evidence={data.evidence}
        />

        <EntitiesSection
          entities={data.entities}
          title={title("entities", "Entities")}
        />

        <TimelineSection
          timeline={data.timeline}
          title={title("timeline", "Investigation Timeline")}
        />

        <DisclaimerPanel
          disclaimer={data.report?.disclaimer}
          safetyGuidance={data.report?.safety_guidance}
          riskCaveat={data.report?.risk_caveat}
          title={data.report?.sections.disclaimer}
        />
      </div>
    </PageContainer>
  );
}
