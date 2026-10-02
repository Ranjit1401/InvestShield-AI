import { AlertTriangle, Loader2, RefreshCw, WifiOff } from "lucide-react";
import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton, SkeletonBlock } from "@/components/ui/skeleton";
import type { ApiError } from "@/services/api-client";
import { cn } from "@/lib/utils";

/* ------------------------------------------------------------------ *
 * Loading
 * ------------------------------------------------------------------ */

/** A card-shaped placeholder, used where a list of records is expected. */
export function CardSkeletonList({ rows = 3, label }: { rows?: number; label: string }) {
  return (
    <SkeletonBlock label={label} className="space-y-3">
      {Array.from({ length: rows }, (_, index) => (
        <Card key={index}>
          <CardContent className="space-y-3 p-5">
            <div className="flex items-center justify-between gap-4">
              <Skeleton className="h-3.5 w-40" />
              <Skeleton className="h-5 w-20" />
            </div>
            <Skeleton className="h-3 w-full" />
            <Skeleton className="h-3 w-2/3" />
          </CardContent>
        </Card>
      ))}
    </SkeletonBlock>
  );
}

/** A stat-tile placeholder for the dashboard overview row. */
export function StatSkeletonRow({ count = 4 }: { count?: number }) {
  return (
    <SkeletonBlock label="Loading summary figures" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {Array.from({ length: count }, (_, index) => (
        <Card key={index}>
          <CardContent className="space-y-2.5 p-5">
            <Skeleton className="h-3 w-24" />
            <Skeleton className="h-7 w-16" />
            <Skeleton className="h-2.5 w-20" />
          </CardContent>
        </Card>
      ))}
    </SkeletonBlock>
  );
}

/** An inline spinner with an accessible label, for submit buttons and toolbars. */
export function InlineSpinner({ label = "Loading" }: { label?: string }) {
  return (
    <span role="status" className="inline-flex items-center gap-2 text-sm text-ink-muted">
      <Loader2 aria-hidden="true" className="size-4 animate-spin" />
      <span>{label}</span>
    </span>
  );
}

/* ------------------------------------------------------------------ *
 * Error
 * ------------------------------------------------------------------ */

/**
 * Turn an {@link ApiError} into wording a user can act on.
 *
 * Only the backend's own `message` and `detail` are shown. Stack traces,
 * driver errors and provider internals are never part of an ApiError, so they
 * cannot reach this text.
 */
function describeError(error: ApiError): { title: string; body: string; lines: string[] } {
  if (error.kind === "network") {
    return {
      title: "InvestShield API is unavailable",
      body: error.message,
      lines: [],
    };
  }
  if (error.kind === "timeout") {
    return {
      title: "The request timed out",
      body: error.message,
      lines: [],
    };
  }
  if (error.kind === "malformed") {
    return {
      title: "Unexpected response from the API",
      body: error.message,
      lines: [],
    };
  }
  if (error.isNotFound) {
    return {
      title: "Investigation not found",
      body: error.message,
      lines: [],
    };
  }
  return {
    title: `Request failed (${error.code})`,
    body: error.message,
    lines: error.detailLines(),
  };
}

export interface ErrorStateProps {
  error: ApiError;
  onRetry?: () => void;
  className?: string;
  /** Extra guidance rendered under the error, such as a link to the dashboard. */
  children?: ReactNode;
}

export function ErrorState({ error, onRetry, className, children }: ErrorStateProps) {
  const { title, body, lines } = describeError(error);
  const offline = error.kind === "network";

  return (
    <Card role="alert" className={cn("border-tone-danger/40", className)}>
      <CardContent className="space-y-3 p-5">
        <div className="flex items-start gap-3">
          {offline ? (
            <WifiOff aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-tone-danger" />
          ) : (
            <AlertTriangle aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-tone-danger" />
          )}
          <div className="min-w-0 flex-1">
            <h2 className="text-sm font-semibold text-ink">{title}</h2>
            <p className="mt-1 text-sm leading-relaxed text-ink-muted">{body}</p>
            {lines.length > 0 ? (
              <ul className="mt-2.5 space-y-1">
                {lines.map((line) => (
                  <li key={line} className="font-mono text-xs text-ink-faint">
                    {line}
                  </li>
                ))}
              </ul>
            ) : null}
            {children}
          </div>
        </div>
        {onRetry ? (
          <Button variant="outline" size="sm" onClick={onRetry}>
            <RefreshCw aria-hidden="true" />
            Try again
          </Button>
        ) : null}
      </CardContent>
    </Card>
  );
}

/* ------------------------------------------------------------------ *
 * Empty
 * ------------------------------------------------------------------ */

export interface EmptyStateProps {
  icon?: ReactNode;
  title: string;
  body: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ icon, title, body, action, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 rounded-[var(--radius-card)] border border-dashed border-hairline px-6 py-12 text-center",
        className,
      )}
    >
      {icon ? <div className="text-ink-faint">{icon}</div> : null}
      <div className="max-w-md space-y-1.5">
        <h2 className="text-sm font-semibold text-ink">{title}</h2>
        <p className="text-sm leading-relaxed text-ink-muted">{body}</p>
      </div>
      {action ? <div className="mt-1">{action}</div> : null}
    </div>
  );
}
