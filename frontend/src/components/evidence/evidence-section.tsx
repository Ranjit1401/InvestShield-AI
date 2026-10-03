import { useMemo } from "react";
import { ArrowDown, FileSearch, Link2 } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/common/state-blocks";
import { VerificationStatusBadge } from "@/components/common/badges";
import { ExternalLink } from "@/components/ui/external-link";
import {
  formatTimestamp,
  humanizeEnum,
  relationLabel,
  sourceTypeLabel,
} from "@/lib/format";
import type { Claim, EvidenceItem, EvidenceResponse, EvidenceSource } from "@/types/api";

/** English fallback, matching the backend's own English section title. */
const FALLBACK_TITLE = "Evidence";

/**
 * A labelled step in the claim → evidence → source chain. The same three
 * labels the graph uses, so the card and the graph read as one system.
 */
function LevelLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-xs font-semibold tracking-wide text-ink-faint uppercase">
      {children}
    </p>
  );
}

/** The visual step from one level of the chain to the next. */
function LevelConnector({ label }: { label: string }) {
  return (
    <div
      aria-hidden="true"
      className="flex items-center gap-2 pl-1 text-ink-faint"
    >
      <span className="h-3 w-px bg-hairline" />
      <ArrowDown className="size-3" />
      <span className="font-mono text-[10px] uppercase tracking-wide">{label}</span>
    </div>
  );
}

/** A single source, with its authority classification spelled out. */
function SourceRow({ source }: { source: EvidenceSource }) {
  return (
    <div className="rounded-md border border-hairline bg-surface p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <ExternalLink href={source.canonical_url || source.url} className="min-w-0 flex-1 text-sm">
          {source.title || source.domain}
        </ExternalLink>
        <Badge tone={source.is_authoritative ? "success" : "neutral"}>
          {source.is_authoritative ? "Authoritative" : "Not authoritative"}
        </Badge>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-faint">
        <span>{sourceTypeLabel(source.source_type)}</span>
        <span className="font-mono">{humanizeEnum(source.source_tier)}</span>
        <span className="font-mono">{source.domain}</span>
        <span>Retrieved {formatTimestamp(source.retrieved_at)}</span>
      </div>
    </div>
  );
}

/** One evidence excerpt with its relationship to the claim. */
function EvidenceRow({ item }: { item: EvidenceItem }) {
  return (
    <li className="rounded-md border border-hairline bg-surface p-3">
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge tone="info">{relationLabel(item.relationship)}</Badge>
        <Badge tone={item.relevance === "HIGH" ? "warning" : "neutral"}>
          {humanizeEnum(item.relevance)} relevance
        </Badge>
        <Badge tone="neutral">{humanizeEnum(item.evidence_type)}</Badge>
        {item.is_proof ? <Badge tone="success">Proof</Badge> : null}
      </div>

      <blockquote className="mt-2.5 border-l-2 border-hairline pl-3 text-sm leading-relaxed text-ink-muted">
        {item.excerpt}
      </blockquote>

      <p className="mt-2 text-xs text-ink-faint">
        From{" "}
        <ExternalLink href={item.source.canonical_url || item.source.url} className="inline">
          {item.source.title || item.source.domain}
        </ExternalLink>
        {item.excerpt_origin ? (
          <>
            {" "}
            · excerpt origin: <span className="font-mono">{item.excerpt_origin}</span>
          </>
        ) : null}
      </p>
    </li>
  );
}

/**
 * The evidence panel.
 *
 * The Claim → Evidence → Source relationship is the point of the product, so
 * the hierarchy is rendered literally: each claim owns a panel, that panel
 * lists the excerpts found for it, and each excerpt names its source. The
 * chain is marked with explicit level labels and connectors so the card
 * reads the same way the evidence graph does.
 *
 * Absence of evidence is reported as absence. A claim with no evidence group,
 * or a group with no items, says so plainly and is never presented as
 * contradicted.
 */
export function EvidenceSection({
  evidence,
  claims,
  title = FALLBACK_TITLE,
}: {
  evidence: EvidenceResponse[];
  claims: Claim[];
  /** Localized section title, from `report.sections.evidence`. */
  title?: string;
}) {
  const claimText = useMemo(() => {
    const map = new Map<string, string>();
    for (const claim of claims) map.set(claim.id, claim.text);
    return map;
  }, [claims]);

  const totalItems = evidence.reduce((sum, group) => sum + group.evidence.length, 0);

  if (evidence.length === 0 || totalItems === 0) {
    return (
      <section aria-labelledby="evidence-heading">
        <h2 id="evidence-heading" className="sr-only">
          {title}
        </h2>
        <EmptyState
          icon={<FileSearch aria-hidden="true" className="size-7" />}
          title="No sufficient evidence was found"
          body="No external record was retrieved that could speak to the extracted claims. This is an absence of evidence, not evidence against the claims, and it is not a contradiction."
        />
      </section>
    );
  }

  return (
    <section aria-labelledby="evidence-heading" className="space-y-3">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id="evidence-heading" className="text-base font-semibold text-ink">
          {title}
        </h2>
        <p className="text-xs text-ink-faint">
          {totalItems} item{totalItems === 1 ? "" : "s"} across {evidence.length} claim
          {evidence.length === 1 ? "" : "s"}
        </p>
      </div>

      <ul className="space-y-4">
        {evidence.map((group) => {
          const text = claimText.get(group.claim_id);
          const sources = group.sources ?? [];
          const supports = group.supports_count ?? 0;
          const contradicts = group.contradicts_count ?? 0;

          return (
            <li key={group.claim_id}>
              <Card>
                <CardContent className="space-y-4 p-4 sm:p-5">
                  {/* Claim */}
                  <div className="space-y-1.5">
                    <LevelLabel>Claim</LevelLabel>
                    <p className="text-sm leading-relaxed font-medium text-ink">
                      {text ? `“${text}”` : <span className="font-mono">{group.claim_id}</span>}
                    </p>
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="font-mono text-xs text-ink-faint">{group.claim_id}</span>
                      {group.verification_status ? (
                        <VerificationStatusBadge status={group.verification_status} />
                      ) : null}
                    </div>
                  </div>

                  <LevelConnector label="Evidence" />

                  {/* Evidence */}
                  <div className="space-y-2">
                    <LevelLabel>Evidence</LevelLabel>
                    {group.evidence.length === 0 ? (
                      <p className="text-sm text-ink-muted">
                        No sufficient evidence found for this claim. Its verification status
                        records why, and no conclusion can be drawn from it.
                      </p>
                    ) : (
                      <>
                        {supports > 0 || contradicts > 0 ? (
                          <p className="text-xs text-ink-faint">
                            {supports} supporting · {contradicts} contradicting
                          </p>
                        ) : null}
                        <ul className="space-y-2">
                          {group.evidence.map((item) => (
                            <EvidenceRow key={item.id} item={item} />
                          ))}
                        </ul>
                      </>
                    )}
                  </div>

                  {/* Sources */}
                  {sources.length > 0 ? (
                    <>
                      <LevelConnector label="Source" />
                      <div className="space-y-2">
                        <LevelLabel>
                          <span className="inline-flex items-center gap-1.5">
                            <Link2 aria-hidden="true" className="size-3" />
                            Sources
                          </span>
                        </LevelLabel>
                        <ul className="space-y-2">
                          {sources.map((source) => (
                            <li key={source.source_id}>
                              <SourceRow source={source} />
                            </li>
                          ))}
                        </ul>
                      </div>
                    </>
                  ) : null}

                  {group.warnings && group.warnings.length > 0 ? (
                    <ul className="space-y-1 border-t border-hairline pt-3">
                      {group.warnings.map((warning) => (
                        <li key={warning} className="text-xs text-ink-faint">
                          {warning}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </CardContent>
              </Card>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
