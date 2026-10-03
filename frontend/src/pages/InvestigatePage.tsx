import { useMemo, useRef, useState, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Ban,
  CheckCircle2,
  Eraser,
  FileText,
  Globe,
  ImageIcon,
  Loader2,
  Send,
  Sparkles,
} from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Input, Label, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Alert } from "@/components/ui/alert";
import { ErrorState } from "@/components/common/state-blocks";
import { cn } from "@/lib/utils";
import { EXAMPLE_TEXT, MAX_TEXT_LENGTH, useTextInvestigation } from "@/hooks/use-text-investigation";
import { MAX_URL_LENGTH, useUrlInvestigation } from "@/hooks/use-url-investigation";
import { useLimits } from "@/hooks/use-investigations";
import { LANGUAGES, type InvestigationInputType, type Language } from "@/types/api";

/** All four input modes, with the phase that will actually process each one. */
const INPUT_MODES = [
  { id: "TEXT" as const, label: "Text", icon: FileText, phase: null },
  { id: "URL" as const, label: "URL", icon: Globe, phase: "Phase 12" },
  { id: "IMAGE" as const, label: "Screenshot", icon: ImageIcon, phase: "Phase 13" },
  { id: "PDF" as const, label: "PDF", icon: FileText, phase: "Phase 14" },
] as const;

/** The one URL used by the "use example" action in URL mode. */
const EXAMPLE_URL = "https://example.com/investment-offer";

type InputModeId = InvestigationInputType;

export function InvestigatePage() {
  const navigate = useNavigate();
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const urlInputRef = useRef<HTMLInputElement>(null);

  const [mode, setMode] = useState<InputModeId>("TEXT");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [language, setLanguage] = useState<Language>("en");
  const [touched, setTouched] = useState(false);

  const limits = useLimits();
  const textApi = useTextInvestigation();
  const urlApi = useUrlInvestigation();

  const api = mode === "URL" ? urlApi : textApi;
  const { submit, isSubmitting, error } = api;

  /**
   * Which modes the backend says it accepts. `GET /api/investigations/limits`
   * is the source of truth; the phase labels below are the documented
   * schedule. If limits has not loaded, only Text is offered, because that is
   * the one input type known to work.
   */
  const supported = useMemo<Set<InvestigationInputType>>(() => {
    const reported = limits.data?.supported_input_types;
    if (!Array.isArray(reported) || reported.length === 0) return new Set(["TEXT"]);
    return new Set(reported);
  }, [limits.data]);

  const maxTextLength =
    typeof limits.data?.max_text_length === "number" ? limits.data.max_text_length : MAX_TEXT_LENGTH;
  const maxUrlLength =
    typeof limits.data?.max_url_length === "number" ? limits.data.max_url_length : MAX_URL_LENGTH;

  const activeMode = INPUT_MODES.find((item) => item.id === mode);

  const value = mode === "URL" ? url : text;
  const trimmed = value.trim();
  const isEmpty = trimmed.length === 0;
  const maxLength = mode === "URL" ? maxUrlLength : maxTextLength;
  const isTooLong = value.length > maxLength;
  const canSubmit = !isEmpty && !isTooLong && !isSubmitting;

  // Client-side guidance only. The backend re-validates and its error is what
  // the user ultimately sees.
  const validationMessage = isEmpty
    ? mode === "URL"
      ? "Enter the URL you want investigated."
      : "Enter the investment content you want investigated."
    : isTooLong
      ? `Content is ${value.length.toLocaleString()} characters. The limit is ${maxLength.toLocaleString()}.`
      : null;

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched(true);
    if (!canSubmit) return;

    void submit(value, language).then((result) => {
      if (result) {
        navigate(`/investigation/${encodeURIComponent(result.investigation_id)}`);
      }
    });
  }

  function useExample() {
    // The example is only ever inserted by an explicit user action.
    if (mode === "URL") {
      setUrl(EXAMPLE_URL);
      urlInputRef.current?.focus();
    } else {
      setText(EXAMPLE_TEXT);
      textareaRef.current?.focus();
    }
    setTouched(false);
  }

  function clear() {
    if (mode === "URL") {
      setUrl("");
      urlInputRef.current?.focus();
    } else {
      setText("");
      textareaRef.current?.focus();
    }
    setTouched(false);
  }

  function handleModeChange(next: InputModeId) {
    setMode(next);
    setTouched(false);
  }

  return (
    <PageContainer>
      <div className="mx-auto max-w-4xl space-y-6">
        <header className="space-y-2">
          <h1 className="text-2xl font-semibold tracking-tight text-ink">Start an investigation</h1>
          <p className="leading-relaxed text-ink-muted">
            Submit the investment content you want examined. The pipeline extracts its claims and
            entities, detects red-flag patterns, verifies the claims against external records and
            assembles the evidence.
          </p>
        </header>

        {/* Input mode selector */}
        <div>
          <h2 id="input-mode-label" className="mb-2 text-sm font-medium text-ink">
            Input mode
          </h2>
          <div
            role="tablist"
            aria-labelledby="input-mode-label"
            className="grid grid-cols-2 gap-2 sm:grid-cols-4"
          >
            {INPUT_MODES.map((item) => {
              const Icon = item.icon;
              const isSupported = supported.has(item.id);
              const isActive = mode === item.id;
              const disabled = !isSupported;

              return (
                <button
                  key={item.id}
                  type="button"
                  role="tab"
                  id={`mode-tab-${item.id}`}
                  aria-selected={isActive}
                  aria-controls="investigation-panel"
                  // An unsupported mode is removed from the tab order rather
                  // than focusable-but-inert.
                  disabled={disabled}
                  onClick={() => handleModeChange(item.id)}
                  className={cn(
                    "flex flex-col items-start gap-1.5 rounded-md border p-3 text-left transition-colors",
                    isActive
                      ? "border-accent-muted bg-surface-raised"
                      : "border-hairline bg-surface hover:border-accent-muted/50",
                    disabled && "cursor-not-allowed opacity-55 hover:border-hairline",
                  )}
                >
                  <span className="flex w-full items-center justify-between gap-2">
                    <span className="flex items-center gap-2 text-sm font-medium text-ink">
                      <Icon aria-hidden="true" className="size-4" />
                      {item.label}
                    </span>
                    {isSupported ? (
                      <Badge tone="success">
                        <CheckCircle2 aria-hidden="true" className="size-3" />
                        Available
                      </Badge>
                    ) : (
                      <Badge tone="neutral">
                        <Ban aria-hidden="true" className="size-3" />
                        Unavailable
                      </Badge>
                    )}
                  </span>
                  <span className="text-xs text-ink-faint">
                    {isSupported
                      ? "Processed today"
                      : item.phase
                        ? `Coming in ${item.phase}`
                        : "Not yet supported"}
                  </span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Input panel */}
        <div
          id="investigation-panel"
          role="tabpanel"
          aria-labelledby={`mode-tab-${mode}`}
          className="space-y-4"
        >
          {mode !== "TEXT" && mode !== "URL" ? (
            <Alert
              tone="info"
              title={`${activeMode?.label ?? "This"} input is not available yet`}
            >
              <p>
                {activeMode?.label ?? "This"} investigation is scheduled for{" "}
                {activeMode?.phase ?? "a later phase"}. The API does not expose an endpoint for it
                yet, so this screen sends no request. Text and URL are fully operational.
              </p>
            </Alert>
          ) : null}

          <form onSubmit={handleSubmit} noValidate>
            <Card>
              <CardHeader>
                <CardTitle>
                  {mode === "URL" ? "Web page to investigate" : "Investment content"}
                </CardTitle>
                <CardDescription>
                  {mode === "URL"
                    ? "Paste the public URL of the page, post or promotion. The page is fetched and its readable text is investigated; the URL itself is checked against the red-flag rules too."
                    : "Paste the message, post or promotion exactly as you received it. Verbatim text lets the pipeline match the claims and red flags it finds back to your content."}
                </CardDescription>
              </CardHeader>

              <CardContent className="space-y-4">
                {mode === "URL" ? (
                  <div className="space-y-2">
                    <Label htmlFor="investigation-url">URL to investigate</Label>
                    <Input
                      id="investigation-url"
                      ref={urlInputRef}
                      type="url"
                      value={url}
                      onChange={(event) => setUrl(event.target.value)}
                      onBlur={() => setTouched(true)}
                      inputMode="url"
                      autoComplete="url"
                      placeholder="https://example.com/investment-offer"
                      invalid={touched && (isEmpty || isTooLong)}
                      aria-describedby="investigation-url-hint investigation-url-count"
                      disabled={isSubmitting}
                    />
                    <p id="investigation-url-hint" className="text-xs text-ink-faint">
                      Public HTTP(S) URLs only. Private, loopback and cloud-metadata
                      addresses are refused by the API.
                    </p>
                  </div>
                ) : (
                  <div className="space-y-2">
                    <Label htmlFor="investigation-text">Content to investigate</Label>
                    <Textarea
                      id="investigation-text"
                      ref={textareaRef}
                      value={text}
                      onChange={(event) => setText(event.target.value)}
                      onBlur={() => setTouched(true)}
                      rows={10}
                      spellCheck={false}
                      placeholder="Paste the investment message you want investigated…"
                      invalid={touched && (isEmpty || isTooLong)}
                      aria-describedby="investigation-text-hint investigation-text-count"
                      disabled={mode !== "TEXT" || isSubmitting}
                    />
                    <p id="investigation-text-hint" className="text-xs text-ink-faint">
                      Up to {maxTextLength.toLocaleString()} characters. Empty or whitespace-only
                      content is refused by the API.
                    </p>
                  </div>
                )}

                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p
                    id={mode === "URL" ? "investigation-url-count" : "investigation-text-count"}
                    className={cn(
                      "font-mono text-xs",
                      isTooLong ? "text-tone-danger" : "text-ink-faint",
                    )}
                  >
                    {value.length.toLocaleString()} / {maxLength.toLocaleString()} characters
                  </p>

                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={useExample}
                      disabled={isSubmitting || (mode !== "TEXT" && mode !== "URL")}
                    >
                      <Sparkles aria-hidden="true" />
                      Use example
                    </Button>
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      onClick={clear}
                      disabled={value.length === 0 || isSubmitting}
                    >
                      <Eraser aria-hidden="true" />
                      Clear
                    </Button>
                  </div>
                </div>

                <details className="rounded-md border border-hairline bg-surface px-3 py-2">
                  <summary className="cursor-pointer text-sm text-ink-muted">
                    {mode === "URL" ? "Example URL" : "Example investment message"}
                  </summary>
                  <p className="mt-2 rounded border border-hairline bg-surface-raised px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-muted">
                    {mode === "URL" ? EXAMPLE_URL : EXAMPLE_TEXT}
                  </p>
                  <p className="mt-1.5 text-xs text-ink-faint">
                    Nothing is submitted until you press the button. Use “Use example” to place
                    this {mode === "URL" ? "URL" : "text"} in the field.
                  </p>
                </details>

                <div className="space-y-2">
                  <Label htmlFor="investigation-language">Content language</Label>
                  <select
                    id="investigation-language"
                    value={language}
                    onChange={(event) => setLanguage(event.target.value as Language)}
                    disabled={isSubmitting}
                    className="h-10 w-full rounded-md border border-hairline bg-surface px-3 text-sm text-ink focus:border-accent-muted sm:w-48"
                  >
                    {LANGUAGES.map((code) => (
                      <option key={code} value={code}>
                        {code.toUpperCase()}
                      </option>
                    ))}
                  </select>
                  <p className="text-xs text-ink-faint">
                    Recorded with the investigation. The report is rendered in English on this
                    version; translation is a later phase.
                  </p>
                </div>

                {touched && validationMessage ? (
                  <Alert tone="warning" title="Check the content before submitting">
                    <p>{validationMessage}</p>
                  </Alert>
                ) : null}

                {error ? (
                  <ErrorState
                    error={error}
                    onRetry={canSubmit ? () => void submit(text, language) : undefined}
                  />
                ) : null}

                <div className="flex flex-wrap items-center gap-3 border-t border-hairline pt-4">
                  <Button
                    type="submit"
                    size="lg"
                    disabled={!canSubmit || (mode !== "TEXT" && mode !== "URL")}
                    aria-busy={isSubmitting}
                  >
                    {isSubmitting ? (
                      <>
                        <Loader2 aria-hidden="true" className="animate-spin" />
                        Investigating…
                      </>
                    ) : (
                      <>
                        <Send aria-hidden="true" />
                        Start investigation
                        <ArrowRight aria-hidden="true" />
                      </>
                    )}
                  </Button>

                  <p className="text-xs text-ink-faint" role="status" aria-live="polite">
                    {isSubmitting
                      ? "Running the investigation pipeline. This can take up to a few minutes while claims are checked against external records. Do not resubmit."
                      : "Runs the full pipeline synchronously, then opens the report."}
                  </p>
                </div>
              </CardContent>
            </Card>
          </form>
        </div>
      </div>
    </PageContainer>
  );
}
