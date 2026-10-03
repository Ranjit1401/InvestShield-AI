import { Flag } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { SeverityBadge } from "@/components/common/badges";
import { EmptyState } from "@/components/common/state-blocks";
import { humanizeEnum } from "@/lib/format";
import type { RedFlag, RiskFactor } from "@/types/api";

/** English fallback, matching the backend's own English section title. */
const FALLBACK_TITLE = "Why This Was Flagged";

/**
 * The red-flag panel answers "Why was this flagged?".
 *
 * Every part of that answer comes from the backend: `name` and `description`
 * say what the pattern is, `rule_reason` says why the rule fired, and
 * `matched_text` proves what in the content triggered it. Nothing is inferred
 * here. A red flag indicates a pattern worth verifying; it is not a finding
 * that the content is fraudulent.
 *
 * When the risk assessment is available, each flag is also linked to the
 * risk factor it produced, so the card can show what the flag contributed
 * to the score, whether evidence backed it, and which claims it attached
 * to — again, all read from the API response, never computed here.
 */
export function RedFlagsSection({
  redFlags,
  title = FALLBACK_TITLE,
  factors = [],
}: {
  redFlags: RedFlag[];
  /** Localized section title, from `report.sections.why_flagged`. */
  title?: string;
  /** Risk factors, used to show each flag's contribution to the score. */
  factors?: RiskFactor[];
}) {
  if (redFlags.length === 0) {
    return (
      <EmptyState
        icon={<Flag aria-hidden="true" className="size-7" />}
        title="No red-flag patterns were detected"
        body="None of the 15 detection rules matched the submitted content. This reflects pattern matching over the text only, and is not an assurance that the investment is legitimate."
      />
    );
  }

  return (
    <section aria-labelledby="red-flags-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="red-flags-heading" className="text-base font-semibold text-ink">
          {title}
        </h2>
        <p className="text-xs text-ink-faint">{redFlags.length} detected</p>
      </div>

      <p className="text-sm leading-relaxed text-ink-muted">
        Each entry below shows what was detected, why its rule triggered, the
        exact text that caused it, and how it contributed to the risk
        assessment.
      </p>

      <ul className="space-y-3">
        {redFlags.map((flag) => {
          const factor = factors.find(
            (candidate) =>
              candidate.origin === "RED_FLAG" &&
              candidate.factor_type === flag.code,
          );

          return (
            <li key={`${flag.code}-${flag.evidence_span.start}`}>
              <Card className="border-tone-danger/25">
                <CardContent className="space-y-3 p-4 sm:p-5">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <h3 className="min-w-0 flex-1 text-sm font-semibold text-ink">
                      {flag.name}
                    </h3>
                    <SeverityBadge severity={flag.severity} />
                  </div>

                  <div className="space-y-1.5">
                    <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                      What was detected
                    </p>
                    <p className="text-sm leading-relaxed text-ink-muted">
                      {flag.description}
                    </p>
                  </div>

                  <div className="rounded-md border border-hairline bg-surface p-3">
                    <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                      Why the rule triggered
                    </p>
                    <p className="mt-1 text-sm leading-relaxed text-ink-muted">
                      {flag.rule_reason}
                    </p>
                  </div>

                  <div className="space-y-2">
                    <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                      Text that caused it
                    </p>
                    {flag.matched_text ? (
                      <p className="rounded border border-hairline bg-surface px-2.5 py-1.5 font-mono text-xs text-ink">
                        “{flag.matched_text}”
                      </p>
                    ) : (
                      <p className="text-sm text-ink-muted">
                        The matched text was not recorded for this flag.
                      </p>
                    )}
                    {flag.additional_spans.length > 0 ? (
                      <ul className="flex flex-wrap gap-1.5" aria-label="Also matched">
                        <li className="sr-only">Also matched:</li>
                        {flag.additional_spans.map((span) => (
                          <li
                            key={`${span.start}-${span.end}`}
                            className="rounded border border-hairline bg-surface px-2 py-0.5 font-mono text-xs text-ink-muted"
                          >
                            {span.text}
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </div>

                  <div className="space-y-2 rounded-md border border-hairline bg-surface p-3">
                    <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                      How it contributed
                    </p>
                    <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-faint">
                      <span className="font-mono">{humanizeEnum(flag.code)}</span>
                      <span>rule weight {flag.weight}</span>
                      {flag.occurrence_count > 1 ? (
                        <span>matched {flag.occurrence_count} times</span>
                      ) : null}
                    </div>

                    {factor ? (
                      <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5 text-xs">
                        <div>
                          <dt className="text-ink-faint">Score contribution</dt>
                          <dd className="font-mono text-ink-muted">
                            +{factor.contribution}
                          </dd>
                        </div>
                        <div>
                          <dt className="text-ink-faint">Evidence backing</dt>
                          <dd className="text-ink-muted">
                            {factor.is_evidence_backed ? (
                              factor.evidence_ids.length > 0 ? (
                                <>
                                  Backed by{" "}
                                  <span className="font-mono">
                                    {factor.evidence_ids.length} evidence
                                  </span>{" "}
                                  item
                                  {factor.evidence_ids.length === 1 ? "" : "s"}
                                </>
                              ) : (
                                "Evidence backed"
                              )
                            ) : (
                              "Not evidence backed"
                            )}
                          </dd>
                        </div>
                        {factor.claim_ids.length > 0 ? (
                          <div>
                            <dt className="text-ink-faint">Linked claims</dt>
                            <dd className="font-mono text-ink-muted">
                              {factor.claim_ids.join(", ")}
                            </dd>
                          </div>
                        ) : null}
                      </dl>
                    ) : (
                      <p className="mt-1 text-xs text-ink-faint">
                        This flag did not produce a risk factor in the assessment.
                      </p>
                    )}
                  </div>
                </CardContent>
              </Card>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
