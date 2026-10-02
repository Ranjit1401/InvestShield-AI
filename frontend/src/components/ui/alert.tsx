import type { HTMLAttributes, ReactNode } from "react";
import { cn } from "@/lib/utils";
import type { Tone } from "@/lib/format";

type AlertTone = Extract<Tone, "neutral" | "info" | "warning" | "danger" | "critical" | "success">;

const toneClasses: Record<AlertTone, string> = {
  neutral: "border-hairline bg-surface-raised text-ink-muted",
  info: "border-tone-info/40 bg-tone-info/8 text-ink",
  success: "border-tone-success/40 bg-tone-success/8 text-ink",
  warning: "border-tone-warning/40 bg-tone-warning/8 text-ink",
  danger: "border-tone-danger/45 bg-tone-danger/10 text-ink",
  critical: "border-tone-critical/50 bg-tone-critical/12 text-ink",
};

const accentClasses: Record<AlertTone, string> = {
  neutral: "bg-tone-neutral",
  info: "bg-tone-info",
  success: "bg-tone-success",
  warning: "bg-tone-warning",
  danger: "bg-tone-danger",
  critical: "bg-tone-critical",
};

export interface AlertProps extends HTMLAttributes<HTMLDivElement> {
  tone?: AlertTone;
  title: string;
  /** Optional non-textual control aligned to the right, such as a retry button. */
  action?: ReactNode;
}

/**
 * Alert is used for limitations, recorded errors and transport failures.
 *
 * The tone is signalled by a left rail as well as by text, so the distinction
 * survives a monochrome or high-contrast rendering.
 */
export function Alert({ tone = "neutral", title, action, className, children, ...props }: AlertProps) {
  return (
    <div
      role="note"
      className={cn("relative overflow-hidden rounded-[var(--radius-card)] border py-4 pr-4 pl-5", toneClasses[tone], className)}
      {...props}
    >
      <span aria-hidden="true" className={cn("absolute inset-y-0 left-0 w-1", accentClasses[tone])} />
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-ink">{title}</p>
          {children ? <div className="mt-1.5 text-sm leading-relaxed text-ink-muted">{children}</div> : null}
        </div>
        {action ? <div className="shrink-0">{action}</div> : null}
      </div>
    </div>
  );
}
