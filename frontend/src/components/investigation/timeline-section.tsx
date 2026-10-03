import { CheckCircle2, CircleDashed, CircleSlash, Loader2, XCircle } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { stageLabel, formatTimestamp, humanizeEnum } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { GraphStage, TimelineEventResponse, TimelineStatus } from "@/types/api";

/** English fallback, matching the backend's own English section title. */
const FALLBACK_TITLE = "Investigation Timeline";

/**
 * The canonical pipeline order.
 *
 * Used only to decide which stage markers to draw, and to show a stage that
 * produced no event. Timestamps and statuses always come from the backend; a
 * stage absent from `timeline` is rendered as "not recorded" rather than being
 * given an invented time.
 */
const PIPELINE_ORDER: GraphStage[] = [
  "input",
  "extraction",
  "red_flags",
  "verification",
  "evidence",
  "risk",
];

const STATUS_ICON: Record<TimelineStatus, typeof CheckCircle2> = {
  COMPLETED: CheckCircle2,
  PARTIAL: CircleDashed,
  STARTED: Loader2,
  FAILED: XCircle,
  SKIPPED: CircleSlash,
};

const STATUS_COLOR: Record<TimelineStatus, string> = {
  COMPLETED: "text-tone-success border-tone-success/50",
  PARTIAL: "text-tone-warning border-tone-warning/50",
  STARTED: "text-tone-info border-tone-info/50",
  FAILED: "text-tone-danger border-tone-danger/50",
  SKIPPED: "text-ink-faint border-hairline",
};

/**
 * The connector below a stage. A completed stage is followed by a
 * success-tinted line so the finished part of the run reads as one
 * continuous progress; a partial stage is followed by a warning line.
 * The line is decoration only — the status word next to each stage
 * remains the authoritative statement.
 */
const CONNECTOR_COLOR: Record<TimelineStatus, string> = {
  COMPLETED: "bg-tone-success/50",
  PARTIAL: "bg-tone-warning/40",
  STARTED: "bg-hairline",
  FAILED: "bg-hairline",
  SKIPPED: "bg-hairline",
};

const STATUS_WORD: Record<TimelineStatus, string> = {
  COMPLETED: "Completed",
  PARTIAL: "Partially completed",
  STARTED: "Started",
  FAILED: "Failed",
  SKIPPED: "Skipped",
};

export function TimelineSection({
  timeline,
  title = FALLBACK_TITLE,
}: {
  timeline: TimelineEventResponse[];
  /** Localized section title, from `report.sections.timeline`. */
  title?: string;
}) {
  // Later events for the same stage replace earlier ones, so a stage that was
  // STARTED then COMPLETED is not shown twice.
  const byStage = new Map<GraphStage, TimelineEventResponse>();
  for (const event of timeline) {
    byStage.set(event.stage, event);
  }

  return (
    <section aria-labelledby="timeline-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="timeline-heading" className="text-base font-semibold text-ink">
          {title}
        </h2>
        <p className="text-xs text-ink-faint">
          {PIPELINE_ORDER.length} stages
        </p>
      </div>

      <Card>
        <CardContent className="p-4 sm:p-5">
          <ol className="space-y-0">
            {PIPELINE_ORDER.map((stage, index) => {
              const event = byStage.get(stage);
              const status: TimelineStatus = event?.status ?? "SKIPPED";
              const Icon = STATUS_ICON[status];
              const isLast = index === PIPELINE_ORDER.length - 1;

              return (
                <li key={stage} className="relative flex gap-3.5 pb-5 last:pb-0">
                  {!isLast ? (
                    <span
                      aria-hidden="true"
                      className={cn(
                        "absolute top-8 bottom-0 left-[15px] w-px",
                        CONNECTOR_COLOR[status],
                      )}
                    />
                  ) : null}

                  <span
                    aria-hidden="true"
                    className={cn(
                      "relative z-10 mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border bg-surface",
                      STATUS_COLOR[status],
                    )}
                  >
                    <Icon className={cn("size-4", status === "STARTED" && "animate-spin")} />
                  </span>

                  <div className="min-w-0 flex-1 pt-1">
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                      <p className="text-sm font-medium text-ink">
                        <span className="mr-2 font-mono text-xs text-ink-faint">
                          {index + 1}
                        </span>
                        {stageLabel(stage)}
                      </p>
                      <p className="font-mono text-xs text-ink-faint">
                        {event ? formatTimestamp(event.at) : "Not recorded"}
                      </p>
                    </div>
                    <p className="mt-0.5 text-sm leading-relaxed text-ink-muted">
                      {event ? event.message : "The pipeline did not report this stage."}
                    </p>
                    <p className="mt-1 text-xs text-ink-faint">
                      <span className="sr-only">Stage status: </span>
                      {STATUS_WORD[status]}
                      {event ? (
                        <>
                          {" · "}
                          <span className="font-mono">{humanizeEnum(stage)}</span>
                        </>
                      ) : null}
                    </p>
                  </div>
                </li>
              );
            })}

            <li className="flex gap-3.5">
              <span
                aria-hidden="true"
                className={cn(
                  "relative z-10 mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border bg-surface",
                  timeline.some((event) => event.stage === "completed")
                    ? "text-tone-success border-tone-success/50"
                    : "text-ink-faint border-hairline",
                )}
              >
                {timeline.some((event) => event.stage === "completed") ? (
                  <CheckCircle2 className="size-4" />
                ) : (
                  <CircleSlash className="size-4" />
                )}
              </span>
              <div className="min-w-0 flex-1 pt-1">
                <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                  <p className="text-sm font-medium text-ink">
                    <span className="mr-2 font-mono text-xs text-ink-faint">
                      {PIPELINE_ORDER.length + 1}
                    </span>
                    Investigation complete
                  </p>
                  <p className="font-mono text-xs text-ink-faint">
                    {byStage.has("completed")
                      ? formatTimestamp(byStage.get("completed")?.at ?? null)
                      : "Not recorded"}
                  </p>
                </div>
                <p className="mt-0.5 text-sm text-ink-muted">
                  {byStage.has("completed")
                    ? (byStage.get("completed")?.message ??
                      "The investigation finished.")
                    : "No completion event was recorded."}
                </p>
              </div>
            </li>
          </ol>
        </CardContent>
      </Card>
    </section>
  );
}
