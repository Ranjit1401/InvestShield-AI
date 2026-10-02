import { cn } from "@/lib/utils";

/**
 * Skeleton placeholders.
 *
 * Every API-driven view has a matching skeleton so a slow or unreachable
 * backend never produces a blank screen. Shimmer is a single restrained
 * animation and is disabled globally under `prefers-reduced-motion`.
 */
export function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      aria-hidden="true"
      className={cn("animate-pulse rounded bg-surface-overlay", className)}
      {...props}
    />
  );
}

/** A labelled group of skeleton rows, announced as busy to assistive tech. */
export function SkeletonBlock({
  label,
  className,
  children,
}: {
  label: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div role="status" aria-live="polite" aria-busy="true" className={cn("space-y-3", className)}>
      <span className="sr-only">{label}</span>
      {children}
    </div>
  );
}
