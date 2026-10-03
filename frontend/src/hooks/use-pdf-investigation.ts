import { useCallback, useRef, useState } from "react";
import type { ApiError } from "@/services/api-client";
import {
  createPdfInvestigation,
  toPublicApiError,
} from "@/services/api-client";
import type { InvestigationResponse, Language } from "@/types/api";

/** Media types the backend accepts for an uploaded PDF. */
export const ALLOWED_PDF_TYPES = ["application/pdf"] as const;

export type SubmitState = "idle" | "submitting" | "succeeded" | "failed";

export interface UsePdfInvestigationResult {
  submitState: SubmitState;
  error: ApiError | null;
  result: InvestigationResponse | null;
  submit: (
    file: File,
    language?: Language,
  ) => Promise<InvestigationResponse | null>;
  reset: () => void;
  /** True while a request is in flight; bind this to the submit button's `disabled`. */
  isSubmitting: boolean;
}

/**
 * Owns the PDF-investigation submission.
 *
 * The upload is a `multipart/form-data` request, so it is sent
 * through the API client's PDF endpoint rather than as JSON.
 * Duplicate submissions are prevented two ways: the returned
 * `isSubmitting` disables the button, and an in-flight guard
 * inside `submit` refuses a second call even if the button is
 * activated by keyboard repeat or a double click that lands
 * before React re-renders.
 */
export function usePdfInvestigation(): UsePdfInvestigationResult {
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<InvestigationResponse | null>(null);

  const inFlight = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(
    async (
      file: File,
      language?: Language,
    ): Promise<InvestigationResponse | null> => {
      if (inFlight.current) return null;

      inFlight.current = true;
      setSubmitState("submitting");
      setError(null);

      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const response = await createPdfInvestigation(
          file,
          language,
          controller.signal,
        );
        setResult(response);
        setSubmitState("succeeded");
        return response;
      } catch (cause) {
        const apiError = toPublicApiError(cause);
        setError(apiError);
        setSubmitState("failed");
        return null;
      } finally {
        inFlight.current = false;
        abortRef.current = null;
      }
    },
    [],
  );

  const reset = useCallback(() => {
    abortRef.current?.abort();
    setSubmitState("idle");
    setError(null);
    setResult(null);
  }, []);

  return {
    submitState,
    error,
    result,
    submit,
    reset,
    isSubmitting: submitState === "submitting",
  };
}
