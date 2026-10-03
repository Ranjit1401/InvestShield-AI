import {
  useMemo,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";
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
import {
  MAX_UPLOAD_BYTES,
  ALLOWED_IMAGE_TYPES,
  useImageInvestigation,
} from "@/hooks/use-image-investigation";
import {
  ALLOWED_PDF_TYPES,
  usePdfInvestigation,
} from "@/hooks/use-pdf-investigation";
import { useLimits } from "@/hooks/use-investigations";
import {
  LANGUAGES,
  type InvestigationInputType,
  type InvestigationResponse,
  type Language,
} from "@/types/api";

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
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [mode, setMode] = useState<InputModeId>("TEXT");
  const [text, setText] = useState("");
  const [url, setUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState<Language>("en");
  const [touched, setTouched] = useState(false);

  const limits = useLimits();
  const textApi = useTextInvestigation();
  const urlApi = useUrlInvestigation();
  const imageApi = useImageInvestigation();
  const pdfApi = usePdfInvestigation();

  // The four submission hooks share every field except `submit`,
  // whose parameter is a string for text and a URL but a `File`
  // for a screenshot or a PDF. The shared fields are read off the
  // union here; the one field that differs is called through
  // `submitCurrent`, which narrows to the right hook for the
  // active mode.
  const api =
    mode === "URL"
      ? urlApi
      : mode === "IMAGE"
        ? imageApi
        : mode === "PDF"
          ? pdfApi
          : textApi;
  const { isSubmitting, error } = api;

  const isUrl = mode === "URL";
  const isImage = mode === "IMAGE";
  const isPdf = mode === "PDF";
  // Screenshot and PDF are both file-upload modes: they share the
  // one file input, the upload byte limit and the file-based
  // validation, differing only in the media types they accept.
  const isFile = isImage || isPdf;

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
  const maxUploadBytes =
    typeof limits.data?.max_upload_bytes === "number" ? limits.data.max_upload_bytes : MAX_UPLOAD_BYTES;

  // The media types the backend accepts, with the documented
  // constants as the fallback before the limits endpoint answers.
  const allowedImageTypes = useMemo(
    () =>
      new Set<string>(
        Array.isArray(limits.data?.allowed_image_types) &&
          limits.data.allowed_image_types.length > 0
          ? limits.data.allowed_image_types
          : ALLOWED_IMAGE_TYPES,
      ),
    [limits.data?.allowed_image_types],
  );
  const allowedPdfTypes = useMemo(
    () =>
      new Set<string>(
        Array.isArray(limits.data?.allowed_pdf_types) &&
          limits.data.allowed_pdf_types.length > 0
          ? limits.data.allowed_pdf_types
          : ALLOWED_PDF_TYPES,
      ),
    [limits.data?.allowed_pdf_types],
  );

  // The media types the active file mode accepts. Only read for
  // a file mode; the non-file branches below never consult it.
  const allowedFileTypes = isPdf ? allowedPdfTypes : allowedImageTypes;
  const allowedFileTypesList = Array.from(allowedFileTypes).join(", ");

  const value = isUrl ? url : text;
  const trimmed = value.trim();

  const isEmpty = isFile ? file === null : trimmed.length === 0;
  const maxLength = isFile
    ? maxUploadBytes
    : isUrl
      ? maxUrlLength
      : maxTextLength;
  const isTooLong = isFile
    ? file !== null && file.size > maxUploadBytes
    : value.length > maxLength;
  // The declared media type is a hint, not a verdict — the server
  // re-checks what the bytes actually parse to — but catching an
  // obvious mismatch here saves an upload.
  const isWrongType =
    isFile && file !== null && !allowedFileTypes.has(file.type);
  const canSubmit = !isEmpty && !isTooLong && !isWrongType && !isSubmitting;

  // Client-side guidance only. The backend re-validates and its error is what
  // the user ultimately sees.
  const validationMessage = isFile
    ? file === null
      ? `Choose the ${isPdf ? "PDF" : "screenshot"} you want investigated.`
      : isTooLong
        ? `The ${isPdf ? "PDF" : "screenshot"} is ${file.size.toLocaleString()} bytes. The limit is ${maxUploadBytes.toLocaleString()} bytes.`
        : isWrongType
          ? `"${file.name}" is reported as ${
              file.type || "an unknown type"
            }. The API accepts ${allowedFileTypesList}.`
          : null
    : isEmpty
      ? isUrl
        ? "Enter the URL you want investigated."
        : "Enter the investment content you want investigated."
      : isTooLong
        ? `Content is ${value.length.toLocaleString()} characters. The limit is ${maxLength.toLocaleString()}.`
        : null;

  function submitCurrent(): Promise<InvestigationResponse | null> {
    if (isImage && file !== null) {
      return imageApi.submit(file, language);
    }
    if (isPdf && file !== null) {
      return pdfApi.submit(file, language);
    }
    return isUrl ? urlApi.submit(value, language) : textApi.submit(value, language);
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setTouched(true);
    if (!canSubmit) return;

    void submitCurrent().then((result) => {
      if (result) {
        navigate(`/investigation/${encodeURIComponent(result.investigation_id)}`);
      }
    });
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null;
    setFile(selected);
    setTouched(true);
  }

  function useExample() {
    // The example is only ever inserted by an explicit user action.
    if (isUrl) {
      setUrl(EXAMPLE_URL);
      urlInputRef.current?.focus();
    } else {
      setText(EXAMPLE_TEXT);
      textareaRef.current?.focus();
    }
    setTouched(false);
  }

  function clear() {
    if (isFile) {
      setFile(null);
      if (fileInputRef.current) fileInputRef.current.value = "";
      fileInputRef.current?.focus();
    } else if (isUrl) {
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
          <form onSubmit={handleSubmit} noValidate>
            <Card>
              <CardHeader>
                <CardTitle>
                  {isUrl
                    ? "Web page to investigate"
                    : isImage
                      ? "Screenshot to investigate"
                      : isPdf
                        ? "PDF to investigate"
                        : "Investment content"}
                </CardTitle>
                <CardDescription>
                  {isUrl
                    ? "Paste the public URL of the page, post or promotion. The page is fetched and its readable text is investigated; the URL itself is checked against the red-flag rules too."
                    : isImage
                      ? "Upload a screenshot of the offer. It is decoded and read by OCR on the server, and the recovered text is investigated as the content it contains."
                      : isPdf
                        ? "Upload a PDF of the offer. It is parsed and read on the server, and the extracted text is investigated as the content it contains."
                        : "Paste the message, post or promotion exactly as you received it. Verbatim text lets the pipeline match the claims and red flags it finds back to your content."}
                </CardDescription>
              </CardHeader>

              <CardContent className="space-y-4">
                {isFile ? (
                  <div className="space-y-2">
                    <Label htmlFor="investigation-file">
                      {isPdf ? "PDF to investigate" : "Screenshot to investigate"}
                    </Label>
                    <Input
                      id="investigation-file"
                      ref={fileInputRef}
                      type="file"
                      accept={allowedFileTypesList}
                      onChange={handleFileChange}
                      onBlur={() => setTouched(true)}
                      invalid={touched && (isTooLong || isWrongType)}
                      aria-describedby="investigation-file-hint investigation-file-count"
                      disabled={isSubmitting}
                    />
                    <p id="investigation-file-hint" className="text-xs text-ink-faint">
                      {isPdf
                        ? `A PDF file up to ${maxUploadBytes.toLocaleString()} bytes. The document is parsed and read on the server; the extracted text is what the pipeline investigates.`
                        : `A PNG, JPEG or WebP file up to ${maxUploadBytes.toLocaleString()} bytes. The screenshot is decoded and read by OCR on the server; the recovered text is what the pipeline investigates.`}
                    </p>
                    {file ? (
                      <p className="font-mono text-xs text-ink-muted" aria-live="polite">
                        {file.name} — {(file.size / 1024).toLocaleString()} KB
                        {file.type ? ` (${file.type})` : ""}
                      </p>
                    ) : null}
                  </div>
                ) : isUrl ? (
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
                    id={
                      isFile
                        ? "investigation-file-count"
                        : isUrl
                          ? "investigation-url-count"
                          : "investigation-text-count"
                    }
                    className={cn(
                      "font-mono text-xs",
                      isTooLong ? "text-tone-danger" : "text-ink-faint",
                    )}
                  >
                    {isFile
                      ? file
                        ? `${file.size.toLocaleString()} / ${maxUploadBytes.toLocaleString()} bytes`
                        : `No file chosen (limit ${maxUploadBytes.toLocaleString()} bytes)`
                        : `${value.length.toLocaleString()} / ${maxLength.toLocaleString()} characters`}
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
                      disabled={(isFile ? file === null : value.length === 0) || isSubmitting}
                    >
                      <Eraser aria-hidden="true" />
                      Clear
                    </Button>
                  </div>
                </div>

                {!isFile ? (
                  <details className="rounded-md border border-hairline bg-surface px-3 py-2">
                    <summary className="cursor-pointer text-sm text-ink-muted">
                      {isUrl ? "Example URL" : "Example investment message"}
                    </summary>
                    <p className="mt-2 rounded border border-hairline bg-surface-raised px-3 py-2 font-mono text-xs leading-relaxed whitespace-pre-wrap text-ink-muted">
                      {isUrl ? EXAMPLE_URL : EXAMPLE_TEXT}
                    </p>
                    <p className="mt-1.5 text-xs text-ink-faint">
                      Nothing is submitted until you press the button. Use “Use example” to place
                      this {isUrl ? "URL" : "text"} in the field.
                    </p>
                  </details>
                ) : null}

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
                    onRetry={canSubmit ? () => void submitCurrent() : undefined}
                  />
                ) : null}

                <div className="flex flex-wrap items-center gap-3 border-t border-hairline pt-4">
                  <Button
                    type="submit"
                    size="lg"
                    disabled={!canSubmit}
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
