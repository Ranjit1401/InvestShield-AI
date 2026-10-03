/**
 * TypeScript mirror of the InvestShield AI backend contract.
 *
 * Every union and field below was read from the live FastAPI application
 * (`app.openapi()` plus the Python enums in `app/schemas/*`), not from
 * documentation prose. If the backend changes a value, change it here too;
 * `docs/API_SPEC.md` is the human-readable companion to this file.
 *
 * Nothing in this file uses `any`. Where the backend is intentionally
 * open-ended (`limits`, `metadata`), the value is typed as `JsonValue` or
 * `JsonObject` and is rendered defensively.
 */

/* ------------------------------------------------------------------ *
 * Primitive JSON shapes
 * ------------------------------------------------------------------ */

/** A JSON scalar. */
export type JsonPrimitive = string | number | boolean | null;

/** Any value that can appear in a JSON document. */
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };

/** A JSON object. Used for intentionally open-ended backend maps. */
export type JsonObject = { [key: string]: JsonValue };

/* ------------------------------------------------------------------ *
 * Controlled vocabularies (exact values from the Python enums)
 * ------------------------------------------------------------------ */

export const INVESTIGATION_STATUSES = ["COMPLETED", "PARTIAL", "FAILED"] as const;
export type InvestigationStatus = (typeof INVESTIGATION_STATUSES)[number];

export const INPUT_TYPES = ["TEXT", "URL", "IMAGE", "PDF"] as const;
export type InvestigationInputType = (typeof INPUT_TYPES)[number];

export const CLAIM_TYPES = [
  "REGULATORY_STATUS",
  "RETURN_PROMISE",
  "PROFIT_PROMISE",
  "PERFORMANCE_CLAIM",
  "CREDENTIAL_CLAIM",
  "COMPANY_CLAIM",
  "PRODUCT_CLAIM",
  "INVESTMENT_OPPORTUNITY",
  "PAYMENT_INSTRUCTION",
  "WITHDRAWAL_CLAIM",
  "AFFILIATION_CLAIM",
  "OWNERSHIP_CLAIM",
  "GUARANTEE_CLAIM",
  "OTHER",
] as const;
export type ClaimType = (typeof CLAIM_TYPES)[number];

export const ENTITY_TYPES = [
  "PERSON",
  "COMPANY",
  "ORGANIZATION",
  "REGULATOR",
  "BROKER",
  "INVESTMENT_ADVISER",
  "PLATFORM",
  "WEBSITE",
  "DOMAIN",
  "PRODUCT",
  "FINANCIAL_INSTRUMENT",
  "LOCATION",
  "SOCIAL_HANDLE",
  "REGISTRATION_NUMBER",
  "BANK_ACCOUNT",
  "UPI_ID",
  "PHONE_NUMBER",
  "EMAIL",
  "OTHER",
] as const;
export type EntityType = (typeof ENTITY_TYPES)[number];

export const RED_FLAG_CODES = [
  "GUARANTEED_RETURN",
  "UNREALISTIC_RETURN",
  "URGENCY_PRESSURE",
  "FAKE_REGULATORY_CLAIM",
  "UNVERIFIED_ADVISER",
  "SUSPICIOUS_URL",
  "THIRD_PARTY_PAYMENT",
  "APK_DOWNLOAD",
  "TELEGRAM_INVESTMENT_GROUP",
  "WHATSAPP_INVESTMENT_GROUP",
  "BORROW_TO_INVEST",
  "WITHDRAWAL_FEE",
  "ACCOUNT_ACTIVATION_FEE",
  "FAKE_PROFIT_SCREENSHOT",
  "IMPERSONATION",
] as const;
export type RedFlagCode = (typeof RED_FLAG_CODES)[number];

export const SEVERITIES = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type Severity = (typeof SEVERITIES)[number];

export const RISK_LEVELS = ["LOW", "MEDIUM", "HIGH", "CRITICAL"] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

export const VERIFICATION_STATUSES = [
  "VERIFIED",
  "UNVERIFIED",
  "CONTRADICTED",
  "INSUFFICIENT_EVIDENCE",
  "NOT_APPLICABLE",
] as const;
export type VerificationStatus = (typeof VERIFICATION_STATUSES)[number];

export const GRAPH_STAGES = [
  "input",
  "extraction",
  "red_flags",
  "verification",
  "evidence",
  "risk",
  "completed",
] as const;
export type GraphStage = (typeof GRAPH_STAGES)[number];

export const TIMELINE_STATUSES = [
  "STARTED",
  "COMPLETED",
  "PARTIAL",
  "FAILED",
  "SKIPPED",
] as const;
export type TimelineStatus = (typeof TIMELINE_STATUSES)[number];

export const EVIDENCE_RELATIONS = [
  "SUPPORTS",
  "CONTRADICTS",
  "IDENTITY_REFERENCE",
  "CONTEXT",
  "MENTIONS",
] as const;
export type EvidenceRelation = (typeof EVIDENCE_RELATIONS)[number];

export const EVIDENCE_RELEVANCES = ["HIGH", "MEDIUM", "LOW"] as const;
export type EvidenceRelevance = (typeof EVIDENCE_RELEVANCES)[number];

export const EVIDENCE_TYPES = [
  "REGULATORY_RECORD",
  "GOVERNMENT_RECORD",
  "EXCHANGE_RECORD",
  "OFFICIAL_ENTITY_SOURCE",
  "SEARCH_RESULT",
] as const;
export type EvidenceType = (typeof EVIDENCE_TYPES)[number];

export const SOURCE_TYPES = [
  "REGULATOR",
  "GOVERNMENT",
  "EXCHANGE",
  "OFFICIAL_ENTITY",
  "TRUSTED_SECONDARY",
  "GENERAL_WEB",
  "UNKNOWN",
] as const;
export type SourceType = (typeof SOURCE_TYPES)[number];

export const SOURCE_TIERS = [
  "TIER_1_PRIMARY_REGULATOR",
  "TIER_2_GOVERNMENT",
  "TIER_3_EXCHANGE",
  "TIER_4_OFFICIAL_ENTITY",
  "TIER_5_TRUSTED_SECONDARY",
  "TIER_6_GENERAL_WEB",
  "UNKNOWN",
] as const;
export type SourceTier = (typeof SOURCE_TIERS)[number];

export const VERIFICATION_FACTOR_TYPES = [
  "CONTRADICTED_CLAIM",
  "UNVERIFIED_CLAIM",
  "INSUFFICIENT_EVIDENCE",
] as const;
export type VerificationFactorType = (typeof VERIFICATION_FACTOR_TYPES)[number];

export const RISK_FACTOR_ORIGINS = ["RED_FLAG", "VERIFICATION"] as const;
export type RiskFactorOrigin = (typeof RISK_FACTOR_ORIGINS)[number];

export const LANGUAGES = ["en", "hi", "mr"] as const;
export type Language = (typeof LANGUAGES)[number];

/* ------------------------------------------------------------------ *
 * Shared fragments
 * ------------------------------------------------------------------ */

/** Character offsets of a fragment inside the submitted content. */
export interface EvidenceSpan {
  start: number;
  end: number;
  text: string;
}

/* ------------------------------------------------------------------ *
 * Health
 * ------------------------------------------------------------------ */

export interface DatabaseProbe {
  connected: boolean;
  dialect: string | null;
  error: string | null;
}

export interface ServiceProbe {
  provider: string;
  configured: boolean;
  available: boolean;
  healthy: boolean | null;
  detail: string | null;
}

export interface HealthResponse {
  status: string;
  app: string;
  version: string;
  environment: string;
  database: DatabaseProbe;
  services: { [service: string]: ServiceProbe };
  time: string;
}

/* ------------------------------------------------------------------ *
 * Limits
 * ------------------------------------------------------------------ */

/**
 * `GET /api/investigations/limits` is declared as an open object
 * (`additionalProperties: true`), so the known keys are optional and any
 * further keys are preserved rather than dropped.
 */
export interface InvestigationLimits {
  supported_input_types?: InvestigationInputType[];
  max_text_length?: number;
  languages?: Language[];
  translation_enabled?: boolean;
  /** Phase 13: the upload byte limit a screenshot may not exceed. */
  max_upload_bytes?: number;
  /** Phase 13: the media types the screenshot endpoint accepts. */
  allowed_image_types?: string[];
  /** Phase 13: the OCR language pack the engine is asked to use. */
  ocr_languages?: string;
  /** Phase 14: the media types the PDF endpoint accepts. */
  allowed_pdf_types?: string[];
  /** Phase 14: how many pages of a PDF are read. */
  pdf_max_pages?: number;
  [key: string]: JsonValue | undefined;
}

/* ------------------------------------------------------------------ *
 * Claims
 * ------------------------------------------------------------------ */

export interface Claim {
  id: string;
  text: string;
  claim_type: ClaimType;
  confidence: number;
  evidence_span: EvidenceSpan;
  entity_ids: string[];
  is_complete_sentence: boolean;
  signals: string[];
  metadata: JsonObject;
  is_verifiable: boolean;
  is_promise: boolean;
  references_entities: boolean;
}

/* ------------------------------------------------------------------ *
 * Entities
 * ------------------------------------------------------------------ */

export interface Entity {
  id: string;
  name: string;
  entity_type: EntityType;
  normalized_name: string;
  confidence: number;
  evidence_span: EvidenceSpan;
  metadata: JsonObject;
}

/* ------------------------------------------------------------------ *
 * Red flags
 * ------------------------------------------------------------------ */

export interface RedFlag {
  code: RedFlagCode;
  name: string;
  description: string;
  severity: Severity;
  weight: number;
  matched_text: string;
  evidence_span: EvidenceSpan;
  rule_reason: string;
  additional_spans: EvidenceSpan[];
  occurrence_count: number;
  sort_key: number[];
}

/* ------------------------------------------------------------------ *
 * Verification
 * ------------------------------------------------------------------ */

export interface VerificationResult {
  claim_id: string;
  claim_type: ClaimType;
  status: VerificationStatus;
  reason: string;
  reason_code: string;
  confidence: number;
  source_ids: string[];
  matched_result_ids: string[];
  queries: string[];
  warnings: string[];
  is_positive: boolean;
  is_inconclusive: boolean;
}

/* ------------------------------------------------------------------ *
 * Evidence
 * ------------------------------------------------------------------ */

export interface EvidenceSource {
  source_id: string;
  result_id: string;
  url: string;
  canonical_url: string;
  domain: string;
  title: string;
  source_type: SourceType;
  source_tier: SourceTier;
  retrieved_at: string;
  position: number;
  source_priority: number;
  is_authoritative: boolean;
}

export interface EvidenceItem {
  id: string;
  claim_id: string;
  verification_status: VerificationStatus;
  claim_type: ClaimType;
  evidence_type: EvidenceType;
  relationship: EvidenceRelation;
  relevance: EvidenceRelevance;
  excerpt: string;
  excerpt_origin: string;
  source: EvidenceSource;
  matched_cue: string | null;
  provider_query: string | null;
  source_id: string;
  result_id: string;
  url: string;
  is_proof: boolean;
}

/** Evidence grouped under the claim it relates to. */
export interface EvidenceResponse {
  claim_id: string;
  verification_status?: VerificationStatus;
  verification_reason_code?: string;
  built_at?: string;
  evidence: EvidenceItem[];
  evidence_count?: number;
  supports_count?: number;
  contradicts_count?: number;
  proof_count?: number;
  source_count?: number;
  context_count?: number;
  sources?: EvidenceSource[];
  warnings?: string[];
}

/* ------------------------------------------------------------------ *
 * Risk
 * ------------------------------------------------------------------ */

export interface RiskFactor {
  id: string;
  origin: RiskFactorOrigin;
  factor_type: RedFlagCode | VerificationFactorType;
  label: string;
  description: string;
  reason: string;
  source: string;
  severity: Severity;
  weight: number;
  contribution: number;
  absorbed_into: string | null;
  claim_ids: string[];
  red_flag_ids: string[];
  evidence_ids: string[];
  source_ids: string[];
  verification_statuses: VerificationStatus[];
  is_uncertainty: boolean;
  is_scoring: boolean;
  is_evidence_backed: boolean;
}

export interface RiskThresholds {
  low_max: number;
  medium_max: number;
  high_max: number;
  critical_max: number;
  ceiling: number;
}

export interface RiskWeights {
  unverified_claim: number;
  contradicted_claim: number;
  insufficient_evidence: number;
  red_flag_weights: { [code: string]: number };
}

export interface RiskAssessment {
  risk_score: number;
  raw_score: number;
  risk_level: RiskLevel;
  factors: RiskFactor[];
  thresholds: RiskThresholds;
  weights: RiskWeights;
  total_factors: number;
  scoring_factors: number;
  deduplicated_factors: number;
  red_flag_factors: number;
  evidence_backed_factors: number;
  uncertainty_factors: number;
  relevant_claims?: number;
  assessed_claims?: number;
  evidence_coverage?: number;
  analysis_completeness?: number;
  assessed_at?: string;
  warnings?: string[];
}

/* ------------------------------------------------------------------ *
 * Timeline, limitations, errors
 * ------------------------------------------------------------------ */

export interface TimelineEventResponse {
  stage: GraphStage;
  status: TimelineStatus;
  message: string;
  at: string;
}

export interface LimitationResponse {
  code: string;
  stage: GraphStage;
  message: string;
}

export interface InvestigationErrorResponse {
  code: string;
  message: string;
  stage: GraphStage;
  error_type: string | null;
}

/* ------------------------------------------------------------------ *
 * Investigation payloads
 * ------------------------------------------------------------------ */

/**
 * Phase 12 provenance for a URL investigation: the page that was
 * fetched, where it resolved, and what it said about itself. Null
 * for every other input kind.
 */
export interface UrlSource {
  submitted_url: string;
  normalized_url: string;
  final_url: string;
  hostname: string;
  resolved_addresses: string[];
  redirected: boolean;
  redirect_count: number;
  page_title: string | null;
  meta_description: string | null;
  http_status: number;
  content_type: string | null;
  is_https: boolean;
  charset: string | null;
  byte_size: number;
  fetched_at: string;
}

/**
 * Phase 13 provenance for a screenshot investigation: the file that
 * was uploaded, what it decoded to, and what the OCR engine recovered.
 * Null for every other input kind. The decode-dependent fields
 * (`detected_content_type`, `format`, `width`, `height`) are `null`
 * when the image was never decoded, which is the normal state of a run
 * whose OCR engine is absent.
 */
export interface ImageSource {
  filename: string | null;
  content_type: string;
  detected_content_type: string | null;
  format: string | null;
  byte_size: number;
  width: number | null;
  height: number | null;
  ocr_language: string;
  /** A fact about the OCR run, not a statement about the image's content. */
  text_recovered: boolean;
  truncated: boolean;
  truncated_at: number | null;
  processed_at: string;
}

/**
 * Phase 14 provenance for a PDF investigation: the file that was
 * uploaded, what it parsed to, and what the extraction engine
 * recovered. Null for every other input kind. The parse-dependent
 * fields (`detected_content_type`, `format`, `page_count`,
 * `pages_processed`) are `null` when the PDF was never parsed, which
 * is the normal state of a run whose PDF library is absent.
 */
export interface PdfSource {
  filename: string | null;
  content_type: string;
  detected_content_type: string | null;
  format: string | null;
  byte_size: number;
  /** Total pages in the document, or `null` when it was never parsed. */
  page_count: number | null;
  /**
   * Pages text was actually read from. Below `page_count` when the
   * page limit skipped the later pages of a long document.
   */
  pages_processed: number | null;
  /** A fact about the extraction run, not a statement about the document's content. */
  text_recovered: boolean;
  truncated: boolean;
  truncated_at: number | null;
  processed_at: string;
}

export interface InvestigationResponse {
  investigation_id: string;
  status: InvestigationStatus;
  input_type: InvestigationInputType;
  language: Language;
  current_stage: string;
  /** Phase 12 provenance for a URL run; `null` otherwise. */
  url_source?: UrlSource | null;
  /** Phase 13 provenance for a screenshot run; `null` otherwise. */
  image_source?: ImageSource | null;
  /** Phase 14 provenance for a PDF run; `null` otherwise. */
  pdf_source?: PdfSource | null;
  claims: Claim[];
  entities: Entity[];
  red_flags: RedFlag[];
  verification_results: VerificationResult[];
  evidence: EvidenceResponse[];
  risk_assessment: RiskAssessment | null;
  timeline: TimelineEventResponse[];
  /** Deduplicated warning codes in first-seen order. Branch on these, not on text. */
  limitations: string[];
  /** Human-readable form of `limitations`, with the stage that recorded each. */
  warnings: LimitationResponse[];
  /** Any entry here means the investigation status is `FAILED`. */
  errors: InvestigationErrorResponse[];
  started_at: string | null;
  /** The backend does not currently populate this field; it is always `null`. */
  completed_at: string | null;
}

export interface InvestigationSummaryResponse {
  investigation_id: string;
  status: InvestigationStatus;
  input_type: InvestigationInputType;
  current_stage: string;
  started_at: string;
  /** Always `null` on the current backend; see `InvestigationResponse`. */
  completed_at: string | null;
  created_at: string;
  claim_count?: number;
  entity_count?: number;
  red_flag_count?: number;
  verification_count?: number;
  source_count?: number;
  evidence_count?: number;
  factor_count?: number;
}

export interface InvestigationListResponse {
  investigations: InvestigationSummaryResponse[];
  total: number;
  limit: number;
  offset: number;
}

/* ------------------------------------------------------------------ *
 * Requests
 * ------------------------------------------------------------------ */

export interface TextInvestigationRequest {
  /** 1-20000 characters. Whitespace-only text is refused by the backend. */
  text: string;
  language?: Language;
}

export interface UrlInvestigationRequest {
  /** The public HTTP(S) URL to investigate, up to 2048 characters. */
  url: string;
  language?: Language;
}

export interface ListInvestigationsParams {
  /** 1-100. The backend rejects anything outside this range. */
  limit?: number;
  /** >= 0. */
  offset?: number;
}

/* ------------------------------------------------------------------ *
 * Error envelope
 * ------------------------------------------------------------------ */

export interface RecordedErrorDetail {
  code: string;
  error_type: string | null;
  stage: GraphStage;
}

export interface FailureDetail {
  errors?: RecordedErrorDetail[];
  submitted_input_type?: string | null;
  supported_input_types?: string[];
}

/** One item of a FastAPI validation `detail` array. */
export interface ValidationIssue {
  type: string;
  loc: (string | number)[];
  msg: string;
}

/**
 * `error.detail` is deliberately polymorphic in the backend: a `FailureDetail`
 * for recorded failures, a `ValidationIssue[]` for request validation, and
 * `null` when there is nothing to add. The client narrows it before use.
 */
export type ErrorDetail = FailureDetail | ValidationIssue[] | null;

export interface ErrorBody {
  code: string;
  message: string;
  detail?: ErrorDetail;
}

export interface ErrorEnvelope {
  error: ErrorBody;
}

/* ------------------------------------------------------------------ *
 * Runtime narrowing helpers
 * ------------------------------------------------------------------ */

/** True when `value` is one of the declared members of a closed union. */
export function isMemberOf<T extends string>(
  value: string,
  allowed: readonly T[],
): value is T {
  return (allowed as readonly string[]).includes(value);
}

/** Narrows an arbitrary string to a known status, falling back safely. */
export function asInvestigationStatus(value: string): InvestigationStatus | null {
  return isMemberOf(value, INVESTIGATION_STATUSES) ? value : null;
}

export function asRiskLevel(value: string): RiskLevel | null {
  return isMemberOf(value, RISK_LEVELS) ? value : null;
}

export function asVerificationStatus(value: string): VerificationStatus | null {
  return isMemberOf(value, VERIFICATION_STATUSES) ? value : null;
}

export function asSeverity(value: string): Severity | null {
  return isMemberOf(value, SEVERITIES) ? value : null;
}

/** True when `detail` is a FastAPI validation array. */
export function isValidationDetail(detail: ErrorDetail): detail is ValidationIssue[] {
  return Array.isArray(detail);
}

/** True when `detail` is a recorded-failure object. */
export function isFailureDetail(detail: ErrorDetail): detail is FailureDetail {
  return detail !== null && !Array.isArray(detail);
}
