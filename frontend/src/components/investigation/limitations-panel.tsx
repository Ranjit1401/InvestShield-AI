import { AlertTriangle, Info, ShieldAlert } from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { stageLabel } from "@/lib/format";
import type { InvestigationErrorResponse, LimitationResponse } from "@/types/api";

/**
 * Limitations and recorded errors.
 *
 * These are never collapsed or hidden behind a disclosure. An investigation
 * that could not check something is materially less useful than one that
 * could, and the user is entitled to see that before reading any conclusion.
 *
 * `limitations` (warning codes) and `warnings` (human-readable, with stage) are
 * the same information in two forms, so only the readable form is rendered and
 * the code is shown alongside it for traceability.
 */
export function LimitationsPanel({
  warnings,
  limitations,
  errors,
}: {
  warnings: LimitationResponse[];
  limitations: string[];
  errors: InvestigationErrorResponse[];
}) {
  if (warnings.length === 0 && errors.length === 0) {
    return null;
  }

  return (
    <section aria-labelledby="limitations-heading" className="space-y-3">
      <h2 id="limitations-heading" className="text-base font-semibold text-ink">
        Limitations and errors
      </h2>

      {errors.length > 0 ? (
        <Alert
          tone="danger"
          title={`Investigation failed (${errors.length} recorded error${errors.length === 1 ? "" : "s"})`}
        >
          <p>
            At least one stage of the investigation could not be completed. The findings below
            are incomplete and should not be read as a full assessment.
          </p>
          <ul className="mt-2 space-y-1.5">
            {errors.map((error) => (
              <li key={`${error.stage}-${error.code}`} className="text-sm">
                <span className="font-mono text-xs text-ink-faint">
                  {stageLabel(error.stage)} · {error.code}
                </span>
                <span className="mt-0.5 block text-ink-muted">{error.message}</span>
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}

      {warnings.length > 0 ? (
        <Alert
          tone="warning"
          title={
            errors.length > 0
              ? "The investigation was only partially completed"
              : "This investigation was only partially completed"
          }
        >
          <p>
            The pipeline ran but could not complete every stage. The findings below are limited
            to what was actually checked.
          </p>
          <ul className="mt-2.5 space-y-2">
            {warnings.map((warning) => (
              <li key={`${warning.stage}-${warning.code}`} className="flex gap-2 text-sm">
                <AlertTriangle
                  aria-hidden="true"
                  className="mt-0.5 size-3.5 shrink-0 text-tone-warning"
                />
                <span>
                  <span className="block text-ink-muted">{warning.message}</span>
                  <span className="mt-0.5 block font-mono text-xs text-ink-faint">
                    {stageLabel(warning.stage)} · {warning.code}
                  </span>
                </span>
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}

      {limitations.length > 0 && warnings.length === 0 ? (
        <Alert tone="neutral" title="Recorded limitations">
          <p>The pipeline reported limitations without a human-readable message.</p>
          <ul className="mt-2 flex flex-wrap gap-1.5">
            {limitations.map((code) => (
              <li
                key={code}
                className="rounded border border-hairline bg-surface px-2 py-0.5 font-mono text-xs text-ink-muted"
              >
                {code}
              </li>
            ))}
          </ul>
        </Alert>
      ) : null}
    </section>
  );
}

/** The standing product disclaimer, shown on every report. */
export function DisclaimerPanel() {
  return (
    <Alert tone="info" title="What this report is, and is not">
      <ul className="space-y-1.5">
        <li className="flex gap-2">
          <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-tone-info" />
          <span>
            InvestShield investigates the <em>claims</em> inside investment content. It reports
            evidence and risk indicators; it does not decide whether an investment is legitimate.
          </span>
        </li>
        <li className="flex gap-2">
          <Info aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-tone-info" />
          <span>An unverified claim is not a fraudulent claim, and missing evidence is not a contradiction.</span>
        </li>
        <li className="flex gap-2">
          <ShieldAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0 text-tone-info" />
          <span>
            Nothing here is financial advice, a recommendation, or a guarantee of accuracy.
          </span>
        </li>
      </ul>
    </Alert>
  );
}
