/**
 * The single place where the frontend talks to the InvestShield API.
 *
 * Components never call `fetch` themselves: every request goes through this
 * module so that error handling, the error-envelope contract, and the base
 * URL live in exactly one place.
 *
 * Design rules enforced here:
 *  - Only `VITE_API_BASE_URL` is read from the environment. No secret, token or
 *    backend credential is ever referenced from the browser bundle.
 *  - Every failure surfaces as an {@link ApiError}. A non-2xx response is
 *    parsed against the documented `{ "error": { code, message, detail } }`
 *    envelope; a body that does not match is still surfaced safely rather than
 *    leaking the raw payload.
 *  - Nothing re-thrown from here contains a stack trace, a SQL fragment, or an
 *    upstream provider message.
 */

import type {
  ErrorBody,
  ErrorDetail,
  FailureDetail,
  GraphStage,
  HealthResponse,
  InvestigationLimits,
  InvestigationListResponse,
  InvestigationResponse,
  ListInvestigationsParams,
  TextInvestigationRequest,
  UrlInvestigationRequest,
  ValidationIssue,
} from "@/types/api";
import { isFailureDetail } from "@/types/api";

/** Development fallback used only when the variable is absent from the build. */
const FALLBACK_BASE_URL = "http://127.0.0.1:8000";

/** Default request timeout. Investigation submission is synchronous server-side. */
const DEFAULT_TIMEOUT_MS = 180_000;

function readBaseUrl(): string {
  const raw = import.meta.env.VITE_API_BASE_URL;
  const candidate = typeof raw === "string" && raw.trim() !== "" ? raw.trim() : FALLBACK_BASE_URL;
  return candidate.replace(/\/+$/, "");
}

export const API_BASE_URL = readBaseUrl();

/* ------------------------------------------------------------------ *
 * Errors
 * ------------------------------------------------------------------ */

/**
 * Why a request failed, in terms the UI can branch on without parsing prose.
 */
export type ApiErrorKind =
  /** The API answered with a non-2xx status. */
  | "http"
  /** The API could not be reached at all (backend down, DNS, CORS, refused). */
  | "network"
  /** The request was aborted or timed out. */
  | "timeout"
  /** A 2xx response whose body was not the JSON shape the contract promises. */
  | "malformed";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  /** HTTP status, or 0 when the request never produced a response. */
  readonly status: number;
  /** The documented `error.code`, or a stable synthetic code. */
  readonly code: string;
  /** The documented `error.detail`, already narrowed to its real shape. */
  readonly detail: ErrorDetail;

  constructor(params: {
    kind: ApiErrorKind;
    status: number;
    code: string;
    message: string;
    detail?: ErrorDetail;
  }) {
    super(params.message);
    this.name = "ApiError";
    this.kind = params.kind;
    this.status = params.status;
    this.code = params.code;
    this.detail = params.detail ?? null;
  }

  /** True when the investigation id does not exist. */
  get isNotFound(): boolean {
    return this.status === 404 || this.code === "INVESTIGATION_NOT_FOUND";
  }

  /** True when the API rejected the request body or query. */
  get isValidation(): boolean {
    return this.status === 422;
  }

  /** True when the API failed to process an otherwise valid request. */
  get isServerFault(): boolean {
    return this.status >= 500;
  }

  /**
   * Flatten the `detail` into short, human-readable lines.
   *
   * Only the documented `code` / `stage` / `msg` fields are surfaced. Nothing
   * else in a response body is ever rendered, so a malformed or unexpected
   * detail cannot inject internal text into the UI.
   */
  detailLines(): string[] {
    if (this.detail === null) return [];

    if (Array.isArray(this.detail)) {
      return this.detail.map((issue: ValidationIssue) => {
        const field = issue.loc.filter((part) => part !== "body" && part !== "query");
        return field.length > 0 ? `${field.join(".")}: ${issue.msg}` : issue.msg;
      });
    }

    if (isFailureDetail(this.detail)) {
      return (this.detail.errors ?? []).map((failure) =>
        failure.error_type
          ? `${failure.stage}: ${failure.code}`
          : `${failure.stage}: ${failure.code}`,
      );
    }

    return [];
  }

  /**
   * The input types the backend currently accepts, when it said so. Used to
   * drive the availability of the input-mode tabs from real API data.
   */
  supportedInputTypes(): string[] {
    if (!isFailureDetail(this.detail)) return [];
    return this.detail.supported_input_types ?? [];
  }
}

/** Coerce anything thrown inside a request into an {@link ApiError}. */
function toApiError(cause: unknown): ApiError {  if (cause instanceof ApiError) return cause;

  if (cause instanceof DOMException && cause.name === "AbortError") {
    return new ApiError({
      kind: "timeout",
      status: 0,
      code: "REQUEST_ABORTED",
      message: "The request was cancelled before the API responded.",
    });
  }

  if (cause instanceof TypeError) {
    // `fetch` rejects with a TypeError for DNS failure, connection refused and
    // a blocked CORS preflight. The browser deliberately hides the cause, so the
    // message stays generic instead of guessing which one happened.
    return new ApiError({
      kind: "network",
      status: 0,
      code: "BACKEND_UNREACHABLE",
      message:
        "Could not reach the InvestShield API. Confirm the backend is running and that VITE_API_BASE_URL points at it.",
    });
  }

  return new ApiError({
    kind: "network",
    status: 0,
    code: "UNEXPECTED_CLIENT_ERROR",
    message: "The request could not be completed.",
  });
}

/**
 * Normalise any thrown value into an {@link ApiError}.
 *
 * Exported so UI code can present a failure from a non-client source (a hook,
 * a JSON parse) with the same guarantees as a request failure.
 */
export function toPublicApiError(cause: unknown): ApiError {
  return toApiError(cause);
}

/* ------------------------------------------------------------------ *
 * Envelope parsing
 * ------------------------------------------------------------------ */

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Validate the parts of `ErrorBody` the UI relies on, ignoring anything else. */
function readErrorBody(payload: unknown): ErrorBody | null {
  if (!isRecord(payload)) return null;
  const error = payload["error"];
  if (!isRecord(error)) return null;

  const code = error["code"];
  const message = error["message"];
  if (typeof code !== "string" || typeof message !== "string") return null;

  // `detail` is passed through only when it is one of the two documented shapes.
  let detail: ErrorDetail = null;
  const raw = error["detail"];
  if (Array.isArray(raw)) {
    detail = raw.filter(isRecord).map((issue) => ({
      type: typeof issue["type"] === "string" ? issue["type"] : "unknown",
      loc: Array.isArray(issue["loc"])
        ? issue["loc"].filter(
            (part): part is string | number =>
              typeof part === "string" || typeof part === "number",
          )
        : [],
      msg: typeof issue["msg"] === "string" ? issue["msg"] : "Invalid value.",
    }));
  } else if (isRecord(raw)) {
    const failure: FailureDetail = {};
    if (Array.isArray(raw["errors"])) {
      failure.errors = raw["errors"].filter(isRecord).map((entry) => ({
        code: typeof entry["code"] === "string" ? entry["code"] : "UNKNOWN",
        error_type: typeof entry["error_type"] === "string" ? entry["error_type"] : null,
        stage: (typeof entry["stage"] === "string" ? entry["stage"] : "input") as GraphStage,
      }));
    }
    if (typeof raw["submitted_input_type"] === "string") {
      failure.submitted_input_type = raw["submitted_input_type"];
    }
    if (Array.isArray(raw["supported_input_types"])) {
      failure.supported_input_types = raw["supported_input_types"].filter(
        (item): item is string => typeof item === "string",
      );
    }
    detail = failure;
  }

  return { code, message, detail };
}

/** Fallback wording for a status the API reached but did not describe. */
function fallbackMessage(status: number): string {
  if (status === 404) return "The requested resource does not exist.";
  if (status === 405) return "That endpoint does not accept this method.";
  if (status === 422) return "The request could not be validated.";
  if (status >= 500) return "The API could not complete the request.";
  return "The request was not completed.";
}

/* ------------------------------------------------------------------ *
 * Core request
 * ------------------------------------------------------------------ */

interface RequestOptions {
  method?: "GET" | "POST";
  body?: unknown;
  signal?: AbortSignal;
  timeoutMs?: number;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = "GET", body, signal, timeoutMs = DEFAULT_TIMEOUT_MS } = options;

  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const onExternalAbort = () => controller.abort();
  signal?.addEventListener("abort", onExternalAbort);

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method,
      headers: {
        // The API accepts JSON only; a form-encoded body is refused with 422.
        Accept: "application/json",
        ...(body === undefined ? {} : { "Content-Type": "application/json" }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch (cause) {
    const aborted = signal?.aborted === true;
    if (aborted) {
      controller.abort();
      throw new ApiError({
        kind: "timeout",
        status: 0,
        code: "REQUEST_CANCELLED",
        message: "The request was cancelled.",
      });
    }
    throw toApiError(cause);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
  }

  // A 204 or an empty 200 body is not part of any documented response.
  const rawText = await response.text().catch(() => "");

  let payload: unknown = null;
  if (rawText.trim() !== "") {
    try {
      payload = JSON.parse(rawText);
    } catch {
      if (response.ok) {
        throw new ApiError({
          kind: "malformed",
          status: response.status,
          code: "MALFORMED_RESPONSE",
          message: "The API returned a response that could not be read as JSON.",
        });
      }
      // A non-2xx body that is not JSON still has a usable status.
    }
  }

  if (!response.ok) {
    const envelope = readErrorBody(payload);
    throw new ApiError({
      kind: "http",
      status: response.status,
      code: envelope?.code ?? `HTTP_${response.status}`,
      message: envelope?.message ?? fallbackMessage(response.status),
      detail: envelope?.detail ?? null,
    });
  }

  if (payload === null) {
    throw new ApiError({
      kind: "malformed",
      status: response.status,
      code: "EMPTY_RESPONSE",
      message: "The API returned an empty response where data was expected.",
    });
  }

  return payload as T;
}

/* ------------------------------------------------------------------ *
 * Endpoints
 * ------------------------------------------------------------------ */

/** `GET /api/health` — liveness, database and provider probes. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return request<HealthResponse>("/api/health", { signal, timeoutMs: 15_000 });
}

/** `GET /api/investigations/limits` — accepted input types and size limits. */
export function getLimits(signal?: AbortSignal): Promise<InvestigationLimits> {
  return request<InvestigationLimits>("/api/investigations/limits", {
    signal,
    timeoutMs: 15_000,
  });
}

/**
 * `POST /api/investigations/url` — submit a URL for investigation.
 */
export function createUrlInvestigation(
  payload: UrlInvestigationRequest,
  signal?: AbortSignal,
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>("/api/investigations/url", {
    method: "POST",
    body: payload,
    signal,
  });
}

/**
 * `POST /api/investigations/text` — submit text for investigation.
 */
export function createTextInvestigation(
  payload: TextInvestigationRequest,
  signal?: AbortSignal,
): Promise<InvestigationResponse> {
  return request<InvestigationResponse>("/api/investigations/text", {
    method: "POST",
    body: payload,
    signal,
  });
}

/** `GET /api/investigations` — server-side page of stored investigations. */
export function getInvestigations(
  params: ListInvestigationsParams = {},
  signal?: AbortSignal,
): Promise<InvestigationListResponse> {
  const query = new URLSearchParams();
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.toString();
  return request<InvestigationListResponse>(
    `/api/investigations${suffix ? `?${suffix}` : ""}`,
    { signal },
  );
}

/** `GET /api/investigations/{id}` — one full investigation report. */
export function getInvestigation(
  investigationId: string,
  signal?: AbortSignal,
): Promise<InvestigationResponse> {
  const encoded = encodeURIComponent(investigationId);
  return request<InvestigationResponse>(`/api/investigations/${encoded}`, { signal });
}
