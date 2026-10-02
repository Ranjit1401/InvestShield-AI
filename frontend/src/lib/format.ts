/**
 * Presentation helpers.
 *
 * These translate backend enum values and timestamps into short display
 * strings. They never invent a value: when the backend gives `null`, the
 * helpers return an explicit "not recorded" marker rather than a guess.
 */

import type {
  ClaimType,
  EntityType,
  EvidenceRelation,
  GraphStage,
  InvestigationInputType,
  RedFlagCode,
  RiskLevel,
  Severity,
  SourceType,
  VerificationStatus,
} from "@/types/api";

/* ------------------------------------------------------------------ *
 * Enum → human label
 * ------------------------------------------------------------------ */

/** `FAKE_REGULATORY_CLAIM` -> `Fake regulatory claim`. */
export function humanizeEnum(value: string): string {
  const text = value.replace(/_/g, " ").trim().toLowerCase();
  if (text === "") return value;
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/* ------------------------------------------------------------------ *
 * Timestamps
 * ------------------------------------------------------------------ */

const NOT_RECORDED = "Not recorded";

/** Absolute local timestamp, or an explicit marker when absent. */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return NOT_RECORDED;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return NOT_RECORDED;
  return parsed.toLocaleString(undefined, {
    year: "numeric",
    month: "short",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/** Short absolute timestamp with seconds, for evidence provenance. */
export function formatTimestamp(value: string | null | undefined): string {
  if (!value) return NOT_RECORDED;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return NOT_RECORDED;
  return parsed.toLocaleString();
}

/** Relative age such as `3 days ago`; falls back to absolute when unparsable. */
export function formatRelative(value: string | null | undefined): string {
  if (!value) return NOT_RECORDED;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return NOT_RECORDED;

  const deltaSeconds = Math.round((Date.now() - parsed.getTime()) / 1000);
  const future = deltaSeconds < 0;
  const seconds = Math.abs(deltaSeconds);

  const units: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, "second"],
    [60, "minute"],
    [24, "hour"],
    [7, "day"],
    [4.34524, "week"],
    [12, "month"],
    [Number.POSITIVE_INFINITY, "year"],
  ];

  let amount = seconds;
  for (const [step, unit] of units) {
    if (amount < step) {
      const rounded = Math.round(amount);
      const text = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }).format(
        future ? rounded : -rounded,
        unit,
      );
      return text;
    }
    amount /= step;
  }
  return formatDateTime(value);
}

/* ------------------------------------------------------------------ *
 * Numeric formatting
 * ------------------------------------------------------------------ */

/** Confidence and score coverage are 0..1 in the backend. */
export function formatPercent(value: number | null | undefined, digits = 0): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return NOT_RECORDED;
  return `${(value * 100).toFixed(digits)}%`;
}

/* ------------------------------------------------------------------ *
 * Enum-specific display
 * ------------------------------------------------------------------ */

const INPUT_TYPE_LABELS: Record<InvestigationInputType, string> = {
  TEXT: "Text",
  URL: "URL",
  IMAGE: "Screenshot",
  PDF: "PDF",
};

export function inputTypeLabel(value: InvestigationInputType): string {
  return INPUT_TYPE_LABELS[value] ?? humanizeEnum(value);
}

const STAGE_LABELS: Record<GraphStage, string> = {
  input: "Input received",
  extraction: "Claims & entities extracted",
  red_flags: "Red flags detected",
  verification: "Claims searched",
  evidence: "Evidence collected",
  risk: "Risk assessed",
  completed: "Investigation complete",
};

export function stageLabel(value: GraphStage): string {
  return STAGE_LABELS[value] ?? humanizeEnum(value);
}

const VERIFICATION_LABELS: Record<VerificationStatus, string> = {
  VERIFIED: "Verified",
  UNVERIFIED: "Unverified",
  CONTRADICTED: "Contradicted",
  INSUFFICIENT_EVIDENCE: "Insufficient evidence",
  NOT_APPLICABLE: "Not applicable",
};

export function verificationLabel(value: VerificationStatus): string {
  return VERIFICATION_LABELS[value] ?? humanizeEnum(value);
}

const RELATION_LABELS: Record<EvidenceRelation, string> = {
  SUPPORTS: "Supports the claim",
  CONTRADICTS: "Contradicts the claim",
  IDENTITY_REFERENCE: "Identifies the entity",
  CONTEXT: "Provides context",
  MENTIONS: "Mentions the claim",
};

export function relationLabel(value: EvidenceRelation): string {
  return RELATION_LABELS[value] ?? humanizeEnum(value);
}

const SOURCE_TYPE_LABELS: Record<SourceType, string> = {
  REGULATOR: "Regulator",
  GOVERNMENT: "Government",
  EXCHANGE: "Exchange",
  OFFICIAL_ENTITY: "Official entity",
  TRUSTED_SECONDARY: "Trusted secondary",
  GENERAL_WEB: "General web",
  UNKNOWN: "Unclassified",
};

export function sourceTypeLabel(value: SourceType): string {
  return SOURCE_TYPE_LABELS[value] ?? humanizeEnum(value);
}

/* ------------------------------------------------------------------ *
 * Colour intent
 * ------------------------------------------------------------------ */

/**
 * Map an enum to a semantic tone. Kept separate from the badge component so
 * that colour intent is decided in one reviewable place.
 */
export type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "critical";

export function riskLevelTone(level: RiskLevel | null | undefined): Tone {
  switch (level) {
    case "LOW":
      return "success";
    case "MEDIUM":
      return "warning";
    case "HIGH":
      return "danger";
    case "CRITICAL":
      return "critical";
    default:
      return "neutral";
  }
}

export function severityTone(severity: Severity | null | undefined): Tone {
  switch (severity) {
    case "LOW":
      return "info";
    case "MEDIUM":
      return "warning";
    case "HIGH":
      return "danger";
    case "CRITICAL":
      return "critical";
    default:
      return "neutral";
  }
}

export function verificationTone(status: VerificationStatus | null | undefined): Tone {
  switch (status) {
    case "VERIFIED":
      return "success";
    case "UNVERIFIED":
      return "warning";
    case "CONTRADICTED":
      return "danger";
    case "INSUFFICIENT_EVIDENCE":
      return "neutral";
    case "NOT_APPLICABLE":
      return "neutral";
    default:
      return "neutral";
  }
}

/** Claim type is descriptive, not a verdict, so it stays neutral. */
export function claimTypeTone(_value: ClaimType): Tone {
  return "neutral";
}

export function entityTypeTone(_value: EntityType): Tone {
  return "info";
}

export function redFlagTone(_code: RedFlagCode): Tone {
  return "danger";
}
