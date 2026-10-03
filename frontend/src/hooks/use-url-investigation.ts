import { useCallback, useRef, useState } from "react";
import type { ApiError } from "@/services/api-client";
import { createUrlInvestigation, toPublicApiError } from "@/services/api-client";
import type { InvestigationResponse, Language, UrlInvestigationRequest } from "@/types/api";

/** Maximum characters the backend accepts for `url`. */
export const MAX_URL_LENGTH = 2048;

export type SubmitState = "idle" | "submitting" | "succeeded" | "failed";

export interface UseUrlInvestigationResult {
  submitState: SubmitState;
  error: ApiError | null;
  result: InvestigationResponse | null;
  submit: (url: string, language?: Language) => Promise<InvestigationResponse | null>;
  reset: () => void;
  /** True while a request is in flight; bind this to the submit button's `disabled`. */
  isSubmitting: boolean;
}

/**
 * Owns the URL-investigation submission.
 *
 * Duplicate submissions are prevented two ways: the returned `isSubmitting`
 * disables the button, and an in-flight guard inside `submit` refuses a second
 * call even if the button is activated by keyboard repeat or a double click
 * that lands before React re-renders.
 */
export function useUrlInvestigation(): UseUrlInvestigationResult {
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<InvestigationResponse | null>(null);

  const inFlight = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(
    async (url: string, language?: Language): Promise<InvestigationResponse | null> => {
      if (inFlight.current) return null;

      inFlight.current = true;
      setSubmitState("submitting");
      setError(null);

      const controller = new AbortController();
      abortRef.current = controller;

      const payload: UrlInvestigationRequest = language
        ? { url, language }
        : { url };

      try {
        const response = await createUrlInvestigation(payload, controller.signal);
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
