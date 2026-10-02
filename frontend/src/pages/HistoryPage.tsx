import { useState } from "react";
import { ArrowLeft, ArrowRight, FileSearch } from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { Button, ButtonLink } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Label } from "@/components/ui/input";
import { Alert } from "@/components/ui/alert";
import {
  CardSkeletonList,
  EmptyState,
  ErrorState,
} from "@/components/common/state-blocks";
import {
  InvestigationList,
  NoResultsNotice,
} from "@/components/investigation/investigation-list";
import { useInvestigations } from "@/hooks/use-investigations";

/** Page sizes offered, all inside the API's accepted 1-100 range. */
const PAGE_SIZES = [10, 20, 50] as const;
type PageSize = (typeof PAGE_SIZES)[number];

export function HistoryPage() {
  const [limit, setLimit] = useState<PageSize>(20);
  const [offset, setOffset] = useState(0);

  const { data, error, isInitialLoading, isLoading, reload } = useInvestigations({
    limit,
    offset,
  });

  const total = data?.total ?? 0;
  const items = data?.investigations ?? [];
  const firstShown = total === 0 ? 0 : offset + 1;
  const lastShown = offset + items.length;
  const canGoBack = offset > 0;
  const canGoForward = offset + limit < total;

  function changePageSize(next: PageSize) {
    setLimit(next);
    // Keep the first record visible when the page size changes.
    setOffset(0);
  }

  return (
    <PageContainer>
      <div className="space-y-6">
        <header className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-tight text-ink">
              Investigation history
            </h1>
            <p className="mt-1.5 leading-relaxed text-ink-muted">
              Every stored investigation, newest first. Paging is performed by the API.
            </p>
          </div>
          <ButtonLink to="/investigate">
            New investigation
            <ArrowRight aria-hidden="true" />
          </ButtonLink>
        </header>

        {error ? (
          <ErrorState error={error} onRetry={reload} />
        ) : (
          <>
            {/* Paging controls use the API's own limit/offset metadata. */}
            <Card>
              <CardContent className="flex flex-wrap items-center justify-between gap-4 p-4">
                <div className="flex items-center gap-2.5">
                  <Label htmlFor="page-size" className="text-xs text-ink-muted">
                    Per page
                  </Label>
                  <select
                    id="page-size"
                    value={limit}
                    onChange={(event) => changePageSize(Number(event.target.value) as PageSize)}
                    className="h-9 rounded-md border border-hairline bg-surface px-2.5 text-sm text-ink focus:border-accent-muted"
                  >
                    {PAGE_SIZES.map((size) => (
                      <option key={size} value={size}>
                        {size}
                      </option>
                    ))}
                  </select>
                </div>

                <p className="text-sm text-ink-muted" role="status" aria-live="polite">
                  {isInitialLoading
                    ? "Loading…"
                    : total === 0
                      ? "No investigations"
                      : `Showing ${firstShown}–${lastShown} of ${total.toLocaleString()}`}
                </p>

                <div className="flex items-center gap-2">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!canGoBack || isLoading}
                    onClick={() => setOffset((value) => Math.max(0, value - limit))}
                    aria-label="Previous page"
                  >
                    <ArrowLeft aria-hidden="true" />
                    Previous
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!canGoForward || isLoading}
                    onClick={() => setOffset((value) => value + limit)}
                    aria-label="Next page"
                  >
                    Next
                    <ArrowRight aria-hidden="true" />
                  </Button>
                </div>
              </CardContent>
            </Card>

            {isInitialLoading ? (
              <CardSkeletonList rows={4} label="Loading investigation history" />
            ) : total === 0 ? (
              <EmptyState
                icon={<FileSearch aria-hidden="true" className="size-7" />}
                title="No investigations yet"
                body="No investigations yet. Start by submitting an investment claim."
                action={
                  <ButtonLink to="/investigate" size="sm">
                    Start your first investigation
                    <ArrowRight aria-hidden="true" />
                  </ButtonLink>
                }
              />
            ) : items.length === 0 ? (
              <NoResultsNotice />
            ) : (
              <>
                <InvestigationList investigations={items} />

                {total === 0 ? null : (
                  <Alert tone="neutral" title="About risk levels in this list">
                    The list endpoint does not return a risk level per investigation, so none is
                    shown here. Open an investigation to see its risk assessment, factors and
                    evidence.
                  </Alert>
                )}
              </>
            )}
          </>
        )}
      </div>
    </PageContainer>
  );
}
