import { useCallback, useRef, useState } from "react";
import type { ApiError} from "@/services/api-client";
import { createTextInvestigation, toPublicApiError } from "@/services/api-client";
import type { InvestigationResponse, Language, TextInvestigationRequest } from "@/types/api";

/** Maximum characters the backend accepts for `text`. */
export const MAX_TEXT_LENGTH = 20_000;

/** The example is a fixture for the button, never pre-filled into the field. */
export const EXAMPLE_TEXT =
  "Guaranteed 40% returns in 30 days. Limited slots available. Send ₹50,000 today to activate your account.";

export type SubmitState = "idle" | "submitting" | "succeeded" | "failed";

export interface UseTextInvestigationResult {
  submitState: SubmitState;
  error: ApiError | null;
  result: InvestigationResponse | null;
  submit: (text: string, language?: Language) => Promise<InvestigationResponse | null>;
  reset: () => void;
  /** True while a request is in flight; bind this to the submit button's `disabled`. */
  isSubmitting: boolean;
}

/**
 * Owns the text-investigation submission.
 *
 * Duplicate submissions are prevented two ways: the returned `isSubmitting`
 * disables the button, and an in-flight guard inside `submit` refuses a second
 * call even if the button is activated by keyboard repeat or a double click
 * that lands before React re-renders.
 */
export function useTextInvestigation(): UseTextInvestigationResult {
  const [submitState, setSubmitState] = useState<SubmitState>("idle");
  const [error, setError] = useState<ApiError | null>(null);
  const [result, setResult] = useState<InvestigationResponse | null>(null);

  const inFlight = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const submit = useCallback(
    async (text: string, language?: Language): Promise<InvestigationResponse | null> => {
      if (inFlight.current) return null;

      inFlight.current = true;
      setSubmitState("submitting");
      setError(null);

      const controller = new AbortController();
      abortRef.current = controller;

      const payload: TextInvestigationRequest = language
        ? { text, language }
        : { text };

      try {
        const response = await createTextInvestigation(payload, controller.signal);
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
