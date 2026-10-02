import { useMemo } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { RiskLevelBadge } from "@/components/common/badges";
import { EmptyState } from "@/components/common/state-blocks";
import { Gauge } from "lucide-react";
import { humanizeEnum, riskLevelTone, severityTone } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { RiskAssessment, RiskFactor, Severity } from "@/types/api";

/** The caveat is fixed product copy, kept verbatim in one place. */
const RISK_CAVEAT =
  "Risk score is a transparent heuristic indicator and is not a probability of fraud or financial loss.";

/** Bar colour per severity, matching the badge tones used elsewhere. */
const SEVERITY_FILL: Record<Severity, string> = {
  LOW: "var(--color-tone-info)",
  MEDIUM: "var(--color-tone-warning)",
  HIGH: "var(--color-tone-danger)",
  CRITICAL: "var(--color-tone-critical)",
};

/** A horizontal bar showing the score against the backend's own ceiling. */
function ScoreMeter({ assessment }: { assessment: RiskAssessment }) {
  const ceiling = assessment.thresholds.ceiling || 100;
  const percent = Math.min(100, Math.max(0, (assessment.risk_score / ceiling) * 100));

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-mono text-4xl font-semibold tabular-nums text-ink">
          {assessment.risk_score}
        </p>
        <p className="text-xs text-ink-faint">ceiling {ceiling}</p>
      </div>

      <div
        role="meter"
        aria-valuenow={assessment.risk_score}
        aria-valuemin={0}
        aria-valuemax={ceiling}
        aria-label="Risk score against the maximum"
        className="mt-2.5 h-2 overflow-hidden rounded-full bg-surface-overlay"
      >
        <div
          className={cn(
            "h-full rounded-full",
            riskLevelTone(assessment.risk_level) === "success" && "bg-tone-success",
            riskLevelTone(assessment.risk_level) === "warning" && "bg-tone-warning",
            riskLevelTone(assessment.risk_level) === "danger" && "bg-tone-danger",
            riskLevelTone(assessment.risk_level) === "critical" && "bg-tone-critical",
          )}
          style={{ width: `${percent}%` }}
        />
      </div>

      <p className="mt-2 text-xs text-ink-faint">
        Raw score {assessment.raw_score}, capped at the ceiling. Thresholds: low ≤{" "}
        {assessment.thresholds.low_max}, medium ≤ {assessment.thresholds.medium_max}, high ≤{" "}
        {assessment.thresholds.high_max}.
      </p>
    </div>
  );
}

/** Aggregate factor contribution by severity. This is real data, not decoration. */
function FactorBreakdownChart({ factors }: { factors: RiskFactor[] }) {
  const data = useMemo(() => {
    const totals = new Map<Severity, number>();
    for (const severity of ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as Severity[]) {
      totals.set(severity, 0);
    }
    for (const factor of factors) {
      totals.set(factor.severity, (totals.get(factor.severity) ?? 0) + factor.contribution);
    }
    return (["CRITICAL", "HIGH", "MEDIUM", "LOW"] as Severity[])
      .map((severity) => ({
        severity: humanizeEnum(severity),
        contribution: totals.get(severity) ?? 0,
        fill: SEVERITY_FILL[severity],
      }))
      .filter((entry) => entry.contribution > 0);
  }, [factors]);

  if (data.length === 0) return null;

  return (
    <div className="mt-4">
      <p className="text-xs font-medium tracking-wide text-ink-faint uppercase">
        Score contribution by severity
      </p>
      <div className="mt-2 h-36 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: -18 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-hairline)" vertical={false} />
            <XAxis
              dataKey="severity"
              tick={{ fill: "var(--color-ink-faint)", fontSize: 11 }}
              axisLine={{ stroke: "var(--color-hairline)" }}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: "var(--color-ink-faint)", fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              cursor={{ fill: "var(--color-surface-overlay)", opacity: 0.4 }}
              contentStyle={{
                background: "var(--color-surface-overlay)",
                border: "1px solid var(--color-hairline)",
                borderRadius: "0.375rem",
                fontSize: "0.75rem",
                color: "var(--color-ink)",
              }}
              labelStyle={{ color: "var(--color-ink)" }}
              formatter={(value: number) => [value, "Contribution"]}
            />
            <Bar dataKey="contribution" radius={[3, 3, 0, 0]}>
              {data.map((entry) => (
                <Cell key={entry.severity} fill={entry.fill} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function FactorRow({ factor }: { factor: RiskFactor }) {
  return (
    <li className="rounded-md border border-hairline bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <p className="min-w-0 flex-1 text-sm font-medium text-ink">{factor.label}</p>
        <div className="flex shrink-0 items-center gap-1.5">
          <Badge tone={severityTone(factor.severity)}>{humanizeEnum(factor.severity)}</Badge>
          <Badge tone="neutral">+{factor.contribution}</Badge>
        </div>
      </div>

      <p className="mt-1.5 text-sm leading-relaxed text-ink-muted">{factor.reason}</p>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-faint">
        <span className="font-mono">{humanizeEnum(factor.factor_type)}</span>
        <span>via {humanizeEnum(factor.origin)}</span>
        {factor.is_evidence_backed ? (
          <span className="text-tone-success">Evidence backed</span>
        ) : (
          <span>Not evidence backed</span>
        )}
        {factor.is_uncertainty ? <span>Counts as uncertainty</span> : null}
        {factor.evidence_ids.length > 0 ? (
          <span>
            {factor.evidence_ids.length} evidence item
            {factor.evidence_ids.length === 1 ? "" : "s"}
          </span>
        ) : null}
        {factor.claim_ids.length > 0 ? (
          <span className="font-mono">{factor.claim_ids.join(", ")}</span>
        ) : null}
      </div>
    </li>
  );
}

/**
 * The risk panel.
 *
 * The score is presented as a transparent indicator with its inputs visible:
 * the factors, their weights, their contributions and the thresholds used. The
 * caveat is always shown, verbatim, and is never reworded into a stronger
 * claim.
 */
export function RiskPanel({ assessment }: { assessment: RiskAssessment | null }) {
  if (!assessment) {
    return (
      <Card>
        <CardContent className="p-5">
          <EmptyState
            icon={<Gauge aria-hidden="true" className="size-7" />}
            title="No risk assessment was produced"
            body="The investigation did not reach the risk stage, so there are no risk indicators to report. See the limitations below for why."
          />
        </CardContent>
      </Card>
    );
  }

  const scoringFactors = assessment.factors.filter((factor) => factor.is_scoring);
  const otherFactors = assessment.factors.filter((factor) => !factor.is_scoring);

  return (
    <section aria-labelledby="risk-heading" className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="risk-heading" className="text-base font-semibold text-ink">
          Risk assessment
        </h2>
        <RiskLevelBadge level={assessment.risk_level} score={assessment.risk_score} />
      </div>

      <Card>
        <CardContent className="p-4 sm:p-5">
          <ScoreMeter assessment={assessment} />

          <p className="mt-3.5 rounded-md border border-hairline bg-surface px-3 py-2.5 text-xs leading-relaxed text-ink-muted">
            {RISK_CAVEAT}
          </p>

          <dl className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3 sm:grid-cols-4">
            <div>
              <dt className="text-xs text-ink-faint">Total factors</dt>
              <dd className="font-mono text-sm text-ink">{assessment.total_factors}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">Scoring factors</dt>
              <dd className="font-mono text-sm text-ink">{assessment.scoring_factors}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">Red-flag factors</dt>
              <dd className="font-mono text-sm text-ink">{assessment.red_flag_factors}</dd>
            </div>
            <div>
              <dt className="text-xs text-ink-faint">Evidence backed</dt>
              <dd className="font-mono text-sm text-ink">{assessment.evidence_backed_factors}</dd>
            </div>
          </dl>

          <FactorBreakdownChart factors={assessment.factors} />
        </CardContent>
      </Card>

      {scoringFactors.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Contributing factors</CardTitle>
            <CardDescription>
              Each factor shows what it contributed and why it was raised.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {scoringFactors.map((factor) => (
                <FactorRow key={factor.id} factor={factor} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}

      {otherFactors.length > 0 ? (
        <Card>
          <CardHeader>
            <CardTitle>Non-scoring observations</CardTitle>
            <CardDescription>
              Recorded for transparency. These did not add to the score.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <ul className="space-y-2">
              {otherFactors.map((factor) => (
                <FactorRow key={factor.id} factor={factor} />
              ))}
            </ul>
          </CardContent>
        </Card>
      ) : null}
    </section>
  );
}
