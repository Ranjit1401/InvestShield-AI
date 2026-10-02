import { useMemo } from "react";
import {
  Activity,
  ArrowRight,
  Database,
  FileSearch,
  Layers,
  Server,
  ShieldCheck,
  Terminal,
} from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { ButtonLink } from "@/components/ui/button";
import { Badge, StatusDot } from "@/components/ui/badge";
import {
  CardSkeletonList,
  EmptyState,
  ErrorState,
  StatSkeletonRow,
} from "@/components/common/state-blocks";
import { InvestigationList } from "@/components/investigation/investigation-list";
import { useHealth, useInvestigationAggregates, useInvestigations } from "@/hooks/use-investigations";
import { humanizeEnum, inputTypeLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { HealthResponse, InvestigationInputType } from "@/types/api";
import type { ApiError } from "@/services/api-client";

/** A single overview figure. */
function StatTile({
  label,
  value,
  hint,
  icon: Icon,
}: {
  label: string;
  value: string;
  hint?: string;
  icon: typeof Layers;
}) {
  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="text-xs tracking-wide text-ink-faint uppercase">{label}</p>
            <p className="mt-1.5 font-mono text-2xl font-semibold tabular-nums text-ink">
              {value}
            </p>
            {hint ? <p className="mt-1 text-xs text-ink-faint">{hint}</p> : null}
          </div>
          <Icon aria-hidden="true" className="size-5 shrink-0 text-ink-faint" />
        </div>
      </CardContent>
    </Card>
  );
}

/** Backend availability and provider configuration, from `GET /api/health`. */
function SystemStatusPanel({
  health,
  isLoading,
  error,
  onRetry,
}: {
  health: HealthResponse | null;
  isLoading: boolean;
  error: ApiError | null;
  onRetry: () => void;
}) {
  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>System status</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2.5" role="status" aria-busy="true">
          <span className="sr-only">Loading system status</span>
          <div className="h-3 w-32 animate-pulse rounded bg-surface-overlay" />
          <div className="h-3 w-full animate-pulse rounded bg-surface-overlay" />
          <div className="h-3 w-4/5 animate-pulse rounded bg-surface-overlay" />
        </CardContent>
      </Card>
    );
  }

  if (error) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>System status</CardTitle>
        </CardHeader>
        <CardContent>
          <ErrorState error={error} onRetry={onRetry} />
        </CardContent>
      </Card>
    );
  }

  if (!health) return null;

  const apiOk = health.status === "ok";

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-3">
          <CardTitle>System status</CardTitle>
          <Badge tone={apiOk ? "success" : "warning"}>
            <StatusDot />
            {humanizeEnum(health.status)}
          </Badge>
        </div>
        <CardDescription>
          InvestShield {health.version} · {health.environment} · {health.app}
        </CardDescription>
      </CardHeader>

      <CardContent className="space-y-4">
        <div className="flex items-start gap-2.5">
          <Database
            aria-hidden="true"
            className={cn(
              "mt-0.5 size-4 shrink-0",
              health.database.connected ? "text-tone-success" : "text-tone-danger",
            )}
          />
          <div className="min-w-0 text-sm">
            <p className="font-medium text-ink">Database</p>
            <p className="text-ink-muted">
              {health.database.connected
                ? `Connected (${health.database.dialect ?? "unknown dialect"})`
                : "Not connected"}
            </p>
          </div>
        </div>

        <div>
          <p className="mb-2 flex items-center gap-1.5 text-xs font-medium tracking-wide text-ink-faint uppercase">
            <Terminal aria-hidden="true" className="size-3" />
            Providers
          </p>
          <ul className="space-y-1.5">
            {Object.entries(health.services).map(([name, probe]) => (
              <li
                key={name}
                className="flex flex-wrap items-center justify-between gap-2 rounded border border-hairline bg-surface px-2.5 py-1.5"
              >
                <span className="min-w-0">
                  <span className="text-sm text-ink">{humanizeEnum(name)}</span>
                  <span className="ml-1.5 font-mono text-xs text-ink-faint">{probe.provider}</span>
                </span>
                <Badge tone={probe.available ? "success" : "neutral"}>
                  {probe.available ? "Available" : "Unavailable"}
                </Badge>
              </li>
            ))}
          </ul>
          {Object.values(health.services).some((probe) => !probe.available) ? (
            <p className="mt-2 text-xs leading-relaxed text-ink-faint">
              Unavailable providers reduce what the pipeline can do. Text investigation still
              works; screenshot and PDF analysis do not.
            </p>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

export function DashboardPage() {
  const health = useHealth();
  const list = useInvestigations({ limit: 5, offset: 0 });
  const aggregates = useInvestigationAggregates(list.data);

  const inputTypeBreakdown = useMemo(
    () =>
      Array.from(aggregates.byInputType.entries()).sort(
        (a, b) => b[1] - a[1],
      ) as [InvestigationInputType, number][],
    [aggregates.byInputType],
  );

  const recent = list.data?.investigations ?? [];
  const isInitialLoad = list.isInitialLoading;

  return (
    <PageContainer>
      <div className="space-y-8">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">Dashboard</h1>
            <p className="mt-1.5 leading-relaxed text-ink-muted">
              Investigation workspace overview, recent activity and backend status.
            </p>
          </div>
          <ButtonLink to="/investigate">
            New investigation
            <ArrowRight aria-hidden="true" />
          </ButtonLink>
        </header>

        {/* Overview */}
        <section aria-labelledby="overview-heading">
          <h2 id="overview-heading" className="sr-only">
            Overview
          </h2>

          {list.error ? (
            <ErrorState error={list.error} onRetry={list.reload} />
          ) : isInitialLoad ? (
            <StatSkeletonRow />
          ) : (
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatTile
                label="Total investigations"
                value={aggregates.total.toLocaleString()}
                hint="Across all stored investigations"
                icon={Layers}
              />
              <StatTile
                label="On this page"
                value={recent.length.toLocaleString()}
                hint={`Most recent ${recent.length || 0} of ${aggregates.total.toLocaleString()}`}
                icon={FileSearch}
              />
              <StatTile
                label="Input modes"
                value={inputTypeBreakdown.length.toLocaleString()}
                hint={
                  inputTypeBreakdown.length > 0
                    ? inputTypeBreakdown
                        .map(([type, count]) => `${inputTypeLabel(type)} ${count}`)
                        .join(" · ")
                    : "No investigations yet"
                }
                icon={Activity}
              />
              <StatTile
                label="API"
                value={
                  health.error ? "Offline" : health.data ? humanizeEnum(health.data.status) : "—"
                }
                hint={health.data ? `Version ${health.data.version}` : "Checking availability"}
                icon={Server}
              />
            </div>
          )}
        </section>

        <div className="grid gap-6 lg:grid-cols-3">
          {/* Recent investigations */}
          <section aria-labelledby="recent-heading" className="lg:col-span-2">
            <div className="mb-3 flex items-baseline justify-between gap-3">
              <h2 id="recent-heading" className="text-base font-semibold text-ink">
                Recent investigations
              </h2>
              {aggregates.total > recent.length ? (
                <ButtonLink to="/history" variant="ghost" size="sm">
                  View all {aggregates.total.toLocaleString()}
                  <ArrowRight aria-hidden="true" />
                </ButtonLink>
              ) : null}
            </div>

            {list.error ? null : isInitialLoad ? (
              <CardSkeletonList rows={3} label="Loading recent investigations" />
            ) : recent.length === 0 ? (
              <EmptyState
                icon={<ShieldCheck aria-hidden="true" className="size-7" />}
                title="No investigations yet"
                body="Your investigation workspace is ready. Submit an investment claim to see claims, entities, red flags, evidence and risk indicators."
                action={
                  <ButtonLink to="/investigate" size="sm">
                    Start your first investigation
                    <ArrowRight aria-hidden="true" />
                  </ButtonLink>
                }
              />
            ) : (
              <InvestigationList investigations={recent} />
            )}
          </section>

          {/* System status */}
          <section aria-labelledby="status-heading">
            <h2 id="status-heading" className="sr-only">
              System status
            </h2>
            <SystemStatusPanel
              health={health.data}
              isLoading={health.isInitialLoading}
              error={health.error}
              onRetry={health.reload}
            />

            <Card className="mt-4">
              <CardHeader>
                <CardTitle>Risk distribution</CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm leading-relaxed text-ink-muted">
                  Not available yet. The list endpoint returns status and per-record counts, but
                  not the risk level for each investigation, so there is no real distribution to
                  chart yet. Open an individual investigation to see its risk assessment.
                </p>
                <ButtonLink to="/history" variant="outline" size="sm" className="mt-3">
                  Browse investigations
                  <ArrowRight aria-hidden="true" />
                </ButtonLink>
              </CardContent>
            </Card>
          </section>
        </div>
      </div>
    </PageContainer>
  );
}
