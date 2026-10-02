import { Badge, StatusDot } from "@/components/ui/badge";
import {
  riskLevelTone,
  severityTone,
  verificationTone,
} from "@/lib/format";
import type {
  InvestigationStatus,
  RiskLevel,
  Severity,
  VerificationStatus,
} from "@/types/api";

/** Investigation status. `PARTIAL` and `FAILED` read differently from `COMPLETED`. */
export function InvestigationStatusBadge({ status }: { status: InvestigationStatus }) {
  const tone =
    status === "COMPLETED" ? "success" : status === "PARTIAL" ? "warning" : "danger";
  return (
    <Badge tone={tone}>
      <StatusDot />
      {status.charAt(0) + status.slice(1).toLowerCase()}
    </Badge>
  );
}

export function RiskLevelBadge({
  level,
  score,
}: {
  level: RiskLevel;
  score?: number;
}) {
  return (
    <Badge tone={riskLevelTone(level)}>
      <StatusDot />
      {level.charAt(0) + level.slice(1).toLowerCase()}
      {typeof score === "number" ? <span className="opacity-70">· {score}</span> : null}
    </Badge>
  );
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <Badge tone={severityTone(severity)}>
      <StatusDot />
      {severity.charAt(0) + severity.slice(1).toLowerCase()}
    </Badge>
  );
}

/**
 * Verification status.
 *
 * `UNVERIFIED` and `INSUFFICIENT_EVIDENCE` are deliberately rendered in
 * neutral and warning tones with plain wording. Neither is presented as a
 * finding of fraud: absence of confirming evidence is not a contradiction.
 */
export function VerificationStatusBadge({ status }: { status: VerificationStatus }) {
  const label: Record<VerificationStatus, string> = {
    VERIFIED: "Verified",
    UNVERIFIED: "Unverified",
    CONTRADICTED: "Contradicted",
    INSUFFICIENT_EVIDENCE: "Insufficient evidence",
    NOT_APPLICABLE: "Not applicable",
  };
  return (
    <Badge tone={verificationTone(status)}>
      <StatusDot />
      {label[status]}
    </Badge>
  );
}

/** A neutral monospace chip for a backend identifier. */
export function IdChip({ value, label }: { value: string; label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 rounded border border-hairline bg-surface px-2 py-0.5 font-mono text-xs text-ink-muted">
      {label ? <span className="text-ink-faint">{label}</span> : null}
      <span className="text-ink">{value}</span>
    </span>
  );
}

/** A small labelled figure used in the header of a report section. */
export function MetaStat({ label, value }: { label: string; value: string }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs tracking-wide text-ink-faint uppercase">{label}</dt>
      <dd className="mt-0.5 truncate text-sm text-ink">{value}</dd>
    </div>
  );
}
