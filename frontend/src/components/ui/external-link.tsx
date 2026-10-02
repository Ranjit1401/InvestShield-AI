import { ExternalLink as ExternalLinkIcon } from "lucide-react";
import { cn } from "@/lib/utils";

/**
 * A link to a source outside the app.
 *
 * Safety properties that matter for evidence links, which are attacker-influenced
 * URLs:
 *  - `rel="noopener noreferrer"` prevents the opened page from reaching back
 *    through `window.opener`.
 *  - `referrerPolicy="no-referrer"` withholds the page URL from the destination,
 *    which would otherwise carry the user's investigation id.
 *  - The scheme is checked before rendering. Only `http` and `https` links are
 *    linked at all; anything else (a `javascript:` or `data:` URL that reached
 *    the evidence store) is shown as inert text.
 */
export interface ExternalLinkProps {
  href: string;
  children: React.ReactNode;
  className?: string;
  /** Show the full URL beneath the label. Off by default on narrow screens. */
  showUrl?: boolean;
}

function isSafeHttpUrl(href: string): boolean {
  try {
    const parsed = new URL(href);
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

export function ExternalLink({ href, children, className, showUrl = false }: ExternalLinkProps) {
  if (!isSafeHttpUrl(href)) {
    return (
      <span className={cn("text-ink-muted underline decoration-dotted", className)}>{children}</span>
    );
  }

  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      referrerPolicy="no-referrer"
      className={cn(
        "inline-flex min-w-0 flex-col gap-0.5 text-accent underline decoration-accent/40 underline-offset-2 transition-colors hover:decoration-accent",
        className,
      )}
    >
      <span className="inline-flex min-w-0 items-center gap-1 break-words">
        <span className="truncate">{children}</span>
        <ExternalLinkIcon aria-hidden="true" className="size-3.5 shrink-0 opacity-70" />
      </span>
      {showUrl ? <span className="truncate font-mono text-xs text-ink-faint">{href}</span> : null}
    </a>
  );
}
