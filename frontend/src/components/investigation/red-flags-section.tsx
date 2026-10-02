import { Flag } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { SeverityBadge } from "@/components/common/badges";
import { EmptyState } from "@/components/common/state-blocks";
import { humanizeEnum } from "@/lib/format";
import type { RedFlag } from "@/types/api";

/**
 * The red-flag panel answers "Why was this flagged?".
 *
 * Every part of that answer comes from the backend: `name` and `description`
 * say what the pattern is, `rule_reason` says why the rule fired, and
 * `matched_text` proves what in the content triggered it. Nothing is inferred
 * here. A red flag indicates a pattern worth verifying; it is not a finding
 * that the content is fraudulent.
 */
export function RedFlagsSection({ redFlags }: { redFlags: RedFlag[] }) {
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
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="red-flags-heading" className="text-base font-semibold text-ink">
          Red flags
        </h2>
        <p className="text-xs text-ink-faint">{redFlags.length} detected</p>
      </div>

      <ul className="space-y-3">
        {redFlags.map((flag) => (
          <li key={`${flag.code}-${flag.evidence_span.start}`}>
            <Card className="border-tone-danger/25">
              <CardContent className="space-y-3 p-4 sm:p-5">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <h3 className="min-w-0 flex-1 text-sm font-semibold text-ink">{flag.name}</h3>
                  <SeverityBadge severity={flag.severity} />
                </div>

                <p className="text-sm leading-relaxed text-ink-muted">{flag.description}</p>

                <div className="rounded-md border border-hairline bg-surface p-3">
                  <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                    Why it was flagged
                  </p>
                  <p className="mt-1 text-sm leading-relaxed text-ink-muted">{flag.rule_reason}</p>
                </div>

                <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-xs text-ink-faint">
                  <span className="font-mono">{humanizeEnum(flag.code)}</span>
                  <span>weight {flag.weight}</span>
                  {flag.occurrence_count > 1 ? (
                    <span>matched {flag.occurrence_count} times</span>
                  ) : null}
                </div>

                {flag.matched_text ? (
                  <div>
                    <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                      Matched text
                    </p>
                    <p className="mt-1 rounded border border-hairline bg-surface px-2.5 py-1.5 font-mono text-xs text-ink">
                      “{flag.matched_text}”
                    </p>
                  </div>
                ) : null}

                {flag.additional_spans.length > 0 ? (
                  <div>
                    <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
                      Also matched
                    </p>
                    <ul className="mt-1 flex flex-wrap gap-1.5">
                      {flag.additional_spans.map((span) => (
                        <li
                          key={`${span.start}-${span.end}`}
                          className="rounded border border-hairline bg-surface px-2 py-0.5 font-mono text-xs text-ink-muted"
                        >
                          {span.text}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}
