import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { VerificationStatusBadge } from "@/components/common/badges";
import { EmptyState } from "@/components/common/state-blocks";
import { MessageSquareQuote } from "lucide-react";
import { formatPercent, humanizeEnum, verificationLabel } from "@/lib/format";
import type { Claim, VerificationResult } from "@/types/api";

export interface ClaimsSectionProps {
  claims: Claim[];
  verificationResults: VerificationResult[];
  /** Ids of claims that an evidence group exists for. */
  evidenceClaimIds: Set<string>;
  /** Show the section heading; false when embedded in a parent panel. */
  showHeading?: boolean;
  /** Localized section title, from `report.sections.claims`. */
  title?: string;
}

function findResult(
  claimId: string,
  results: VerificationResult[],
): VerificationResult | undefined {
  return results.find((result) => result.claim_id === claimId);
}

/**
 * The claims panel.
 *
 * Wording rule: an unverified claim is described as *unverified*, never as
 * fraudulent. The backend's `VerificationResult.reason` is shown verbatim
 * because it is the API's own explanation of what could and could not be
 * established.
 */
export function ClaimsSection({
  claims,
  verificationResults,
  evidenceClaimIds,
  showHeading = true,
  title = "Claims",
}: ClaimsSectionProps) {
  if (claims.length === 0) {
    return (
      <section aria-labelledby="claims-heading">
        {showHeading ? (
          <h2 id="claims-heading" className="sr-only">
            Claims
          </h2>
        ) : null}
        <EmptyState
          icon={<MessageSquareQuote aria-hidden="true" className="size-7" />}
          title="No claims were extracted"
          body="The investigation pipeline did not identify any checkable claims in the submitted content. This is a statement about the extraction step, not a judgement that the content is safe."
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="claims-heading" className="space-y-3">
      {showHeading ? (
        <div className="flex items-baseline justify-between gap-3">
          <h2 id="claims-heading" className="text-base font-semibold text-ink">
            {title}
          </h2>
          <p className="text-xs text-ink-faint">
            {claims.length} extracted
          </p>
        </div>
      ) : null}

      <ul className="space-y-3">
        {claims.map((claim) => {
          const result = findResult(claim.id, verificationResults);
          const status = result?.status ?? null;
          const hasEvidence = evidenceClaimIds.has(claim.id);

          return (
            <li key={claim.id}>
              <Card>
                <CardContent className="space-y-3 p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <p className="min-w-0 flex-1 text-sm leading-relaxed font-medium text-ink">
                      “{claim.text}”
                    </p>
                    <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                      {status ? <VerificationStatusBadge status={status} /> : null}
                    </div>
                  </div>

                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-xs text-ink-faint">
                    <span className="font-mono text-ink-muted">{claim.id}</span>
                    <Badge tone="neutral">{humanizeEnum(claim.claim_type)}</Badge>
                    <span>
                      Extraction confidence{" "}
                      <span className="text-ink-muted">{formatPercent(claim.confidence)}</span>
                    </span>
                    {claim.is_verifiable ? (
                      <span>Checkable against an external record</span>
                    ) : (
                      <span>No external register can settle this claim</span>
                    )}
                    {hasEvidence ? (
                      <span className="text-tone-info">Evidence attached</span>
                    ) : null}
                  </div>

                  {result ? (
                    <div className="rounded-md border border-hairline bg-surface p-3">
                      <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                        Verification result
                      </p>
                      <p className="mt-1 text-sm leading-relaxed text-ink-muted">{result.reason}</p>
                      <p className="mt-2 flex flex-wrap gap-x-3 gap-y-1 font-mono text-xs text-ink-faint">
                        <span>{result.reason_code}</span>
                        <span>
                          {verificationLabel(result.status)} · confidence{" "}
                          {formatPercent(result.confidence)}
                        </span>
                      </p>
                      {result.warnings.length > 0 ? (
                        <ul className="mt-2 space-y-1">
                          {result.warnings.map((warning) => (
                            <li key={warning} className="text-xs text-ink-faint">
                              {warning}
                            </li>
                          ))}
                        </ul>
                      ) : null}
                    </div>
                  ) : null}
                </CardContent>
              </Card>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
