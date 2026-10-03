import { Building2, User } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { EmptyState } from "@/components/common/state-blocks";
import { formatPercent, humanizeEnum } from "@/lib/format";
import type { Entity, JsonObject } from "@/types/api";

/**
 * Render a single metadata entry.
 *
 * Entity metadata is a free-form object chosen by the extractors. Only scalars
 * are rendered; an object or array is summarised by its type rather than being
 * dumped as `[object Object]`. Values are shown as data, never as instructions.
 */
function renderMetadataValue(value: string | number | boolean | null): string {
  if (value === null) return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

function MetadataPairs({ metadata }: { metadata: JsonObject }) {
  const entries = Object.entries(metadata).filter(
    (entry): entry is [string, string | number | boolean | null] =>
      entry[1] === null ||
      typeof entry[1] === "string" ||
      typeof entry[1] === "number" ||
      typeof entry[1] === "boolean",
  );

  if (entries.length === 0) return null;

  return (
    <dl className="mt-2 flex flex-wrap gap-x-5 gap-y-1.5">
      {entries.map(([key, value]) => (
        <div key={key} className="min-w-0">
          <dt className="text-xs text-ink-faint">{humanizeEnum(key)}</dt>
          <dd className="truncate font-mono text-xs text-ink-muted">
            {renderMetadataValue(value)}
          </dd>
        </div>
      ))}
    </dl>
  );
}

export function EntitiesSection({
  entities,
  title = "Entities",
}: {
  entities: Entity[];
  /** Localized section title, from `report.sections.entities`. */
  title?: string;
}) {
  if (entities.length === 0) {
    return (
      <EmptyState
        icon={<User aria-hidden="true" className="size-7" />}
        title="No entities were identified"
        body="No person, organisation, registration number or platform was recognised in the submitted content."
      />
    );
  }

  return (
    <section aria-labelledby="entities-heading" className="space-y-3">
      <div className="flex items-baseline justify-between gap-3">
        <h2 id="entities-heading" className="text-base font-semibold text-ink">
          {title}
        </h2>
        <p className="text-xs text-ink-faint">{entities.length} identified</p>
      </div>

      <ul className="grid gap-3 sm:grid-cols-2">
        {entities.map((entity) => (
          <li key={entity.id}>
            <Card className="h-full">
              <CardContent className="space-y-2.5 p-4">
                <div className="flex items-start gap-2.5">
                  <Building2 aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-tone-info" />
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-sm font-medium text-ink">{entity.name}</p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      <Badge tone="info">{humanizeEnum(entity.entity_type)}</Badge>
                      <span className="text-xs text-ink-faint">
                        confidence {formatPercent(entity.confidence)}
                      </span>
                    </div>
                  </div>
                </div>

                {entity.normalized_name &&
                entity.normalized_name.toLowerCase() !== entity.name.toLowerCase() ? (
                  <p className="text-xs text-ink-faint">
                    Normalized as{" "}
                    <span className="font-mono text-ink-muted">{entity.normalized_name}</span>
                  </p>
                ) : null}

                <MetadataPairs metadata={entity.metadata} />

                {entity.evidence_span.text ? (
                  <p className="border-t border-hairline pt-2 text-xs text-ink-faint">
                    In content as{" "}
                    <span className="font-mono text-ink-muted">
                      “{entity.evidence_span.text}”
                    </span>
                  </p>
                ) : null}
              </CardContent>
            </Card>
          </li>
        ))}
      </ul>
    </section>
  );
}
