import { cva, type VariantProps } from "class-variance-authority";
import type { HTMLAttributes } from "react";
import { cn } from "@/lib/utils";

/**
 * Badge carries a single categorical fact.
 *
 * Every tone pairs a tinted background with a text colour at least 4.5:1
 * against it, and every badge also renders a text label — colour is never the
 * only carrier of meaning.
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium tracking-wide whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-tone-neutral/40 bg-tone-neutral/10 text-ink-muted",
        info: "border-tone-info/40 bg-tone-info/10 text-tone-info",
        success: "border-tone-success/40 bg-tone-success/10 text-tone-success",
        warning: "border-tone-warning/40 bg-tone-warning/10 text-tone-warning",
        danger: "border-tone-danger/45 bg-tone-danger/12 text-tone-danger",
        critical:
          "border-tone-critical/50 bg-tone-critical/15 text-tone-critical font-semibold",
      },
      variant: {
        soft: "",
        solid: "",
      },
    },
    defaultVariants: {
      tone: "neutral",
      variant: "soft",
    },
  },
);

export interface BadgeProps
  extends HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, tone, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ tone, variant }), className)} {...props} />;
}

/** A small leading dot, used to reinforce a status without replacing its label. */
export function StatusDot({ className, ...props }: HTMLAttributes<HTMLSpanElement>) {
  return <span aria-hidden="true" className={cn("size-1.5 rounded-full bg-current", className)} {...props} />;
}
