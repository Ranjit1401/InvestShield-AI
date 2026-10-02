/**
 * Render entry for `verify-render.mjs`.
 *
 * Composes the real report components around a real investigation payload so
 * the render check exercises the same tree the result page builds.
 */

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
import { InvestigationList } from "@/components/investigation/investigation-list";
import type { InvestigationResponse, InvestigationSummaryResponse } from "@/types/api";

type RouterLike = React.ComponentType<{ children: React.ReactNode }>;

export function renderReport({
  investigation,
  router: Router,
}: {
  investigation: InvestigationResponse;
  summary: InvestigationSummaryResponse | null;
  router: RouterLike;
}): string {
  const data = investigation;
  const evidenceClaimIds = new Set(
    data.evidence.filter((group) => group.evidence.length > 0).map((g) => g.claim_id),
  );

  return renderToString(
    <Router>
      <div>
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
    </Router>,
  );
}

export function renderList({
  investigation,
  router: Router,
}: {
  investigation: InvestigationSummaryResponse;
  router: RouterLike;
}): string {
  return renderToString(
    <Router>
      <InvestigationList investigations={[investigation]} />
    </Router>,
  );
}

/**
 * Renders every empty state with an otherwise-empty investigation, so the
 * zero-data paths are checked rather than assumed.
 */
export function renderEmptyReport({ router: Router }: { router: RouterLike }): string {
  const empty: InvestigationResponse = {
    investigation_id: "inv_empty_state",
    status: "FAILED",
    input_type: "TEXT",
    language: "en",
    current_stage: "input",
    claims: [],
    entities: [],
    red_flags: [],
    verification_results: [],
    evidence: [],
    risk_assessment: null,
    timeline: [],
    limitations: [],
    warnings: [],
    errors: [
      {
        code: "EXAMPLE_FAILURE",
        message: "A recorded stage failure, used to render the error state.",
        stage: "extraction",
        error_type: null,
      },
    ],
    started_at: null,
    completed_at: null,
  };

  return renderToString(
    <Router>
      <div>
        <InvestigationHeader investigation={empty} />
        <LimitationsPanel warnings={[]} limitations={[]} errors={empty.errors} />
        <RiskPanel assessment={null} />
        <RedFlagsSection redFlags={[]} />
        <ClaimsSection
          claims={[]}
          verificationResults={[]}
          evidenceClaimIds={new Set()}
        />
        <EvidenceSection evidence={[]} claims={[]} />
        <EntitiesSection entities={[]} />
        <TimelineSection timeline={[]} />
      </div>
    </Router>,
  );
}

import { renderToStaticMarkup as renderToString } from "react-dom/server";
