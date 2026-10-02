import { useEffect, useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { Activity, Menu, ShieldCheck, X } from "lucide-react";
import { ButtonLink } from "@/components/ui/button";
import { useHealth } from "@/hooks/use-investigations";
import { cn } from "@/lib/utils";

const NAV_ITEMS = [
  { to: "/dashboard", label: "Dashboard" },
  { to: "/investigate", label: "Investigate" },
  { to: "/history", label: "History" },
] as const;

/**
 * A small live indicator for the API.
 *
 * The landing page deliberately does not use this: it must render and explain
 * the product even when the backend is down.
 */
function ApiStatusPill() {
  const { data, isLoading, error } = useHealth();

  let label: string;
  let tone: string;
  if (isLoading) {
    label = "Checking API";
    tone = "text-ink-faint";
  } else if (error) {
    label = "API offline";
    tone = "text-tone-danger";
  } else if (data?.status === "ok") {
    label = "API online";
    tone = "text-tone-success";
  } else {
    label = "API degraded";
    tone = "text-tone-warning";
  }

  return (
    <span
      className={cn("inline-flex items-center gap-1.5 text-xs font-medium", tone)}
      // The state is also given in text, so this is supplementary, not the
      // only signal.
      role="status"
    >
      <Activity aria-hidden="true" className="size-3.5" />
      {label}
    </span>
  );
}

function Wordmark() {
  return (
    <Link
      to="/"
      className="flex items-center gap-2 text-sm font-semibold tracking-tight text-ink"
    >
      <span className="flex size-7 items-center justify-center rounded bg-accent text-surface">
        <ShieldCheck aria-hidden="true" className="size-4" />
      </span>
      <span>
        InvestShield<span className="text-accent"> AI</span>
      </span>
    </Link>
  );
}

/** Primary navigation. Collapses to a disclosure menu below the `md` breakpoint. */
function TopNav() {
  const [open, setOpen] = useState(false);
  const location = useLocation();

  // Close the mobile menu whenever navigation happens.
  useEffect(() => {
    setOpen(false);
  }, [location.pathname]);

  return (
    <header className="sticky top-0 z-40 border-b border-hairline bg-surface/95 backdrop-blur">
      <div className="mx-auto flex h-14 max-w-7xl items-center gap-4 px-4 sm:px-6">
        <Wordmark />

        <nav aria-label="Primary" className="ml-2 hidden items-center gap-1 md:flex">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                cn(
                  "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-surface-overlay text-ink"
                    : "text-ink-muted hover:bg-surface-raised hover:text-ink",
                )
              }
            >
              {item.label}
            </NavLink>
          ))}
        </nav>

        <div className="ml-auto flex items-center gap-3">
          <div className="hidden sm:block">
            <ApiStatusPill />
          </div>
          <ButtonLink to="/investigate" size="sm" className="hidden sm:inline-flex">
            New investigation
          </ButtonLink>

          <button
            type="button"
            onClick={() => setOpen((value) => !value)}
            aria-expanded={open}
            aria-controls="mobile-nav"
            aria-label={open ? "Close navigation menu" : "Open navigation menu"}
            className="rounded-md p-2 text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink md:hidden"
          >
            {open ? <X aria-hidden="true" className="size-5" /> : <Menu aria-hidden="true" className="size-5" />}
          </button>
        </div>
      </div>

      {open ? (
        <nav
          id="mobile-nav"
          aria-label="Primary mobile"
          className="border-t border-hairline bg-surface px-4 py-3 md:hidden"
        >
          <ul className="space-y-1">
            {NAV_ITEMS.map((item) => (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  className={({ isActive }) =>
                    cn(
                      "block rounded-md px-3 py-2 text-sm font-medium",
                      isActive
                        ? "bg-surface-overlay text-ink"
                        : "text-ink-muted hover:bg-surface-raised hover:text-ink",
                    )
                  }
                >
                  {item.label}
                </NavLink>
              </li>
            ))}
          </ul>
          <div className="mt-3 flex items-center justify-between gap-3 border-t border-hairline pt-3">
            <ApiStatusPill />
            <ButtonLink to="/investigate" size="sm">
              New investigation
            </ButtonLink>
          </div>
        </nav>
      ) : null}
    </header>
  );
}

function Footer() {
  return (
    <footer className="mt-auto border-t border-hairline bg-surface">
      <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-6 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between sm:px-6">
        <p>InvestShield AI — Investigate Before You Invest.</p>
        <p className="max-w-xl sm:text-right">
          Research and risk indicators only. Not financial advice, and not a fraud
          determination.
        </p>
      </div>
    </footer>
  );
}

/**
 * The application shell.
 *
 * A `Skip to content` link is the first focusable element so keyboard users can
 * bypass the navigation on every page.
 */
export function AppShell() {
  return (
    <div className="flex min-h-dvh flex-col bg-surface">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-50 focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-sm focus:font-semibold focus:text-surface"
      >
        Skip to content
      </a>
      <TopNav />
      <main id="main" tabIndex={-1} className="flex-1 focus:outline-none">
        <Outlet />
      </main>
      <Footer />
    </div>
  );
}

/** Standard page gutter and max width. */
export function PageContainer({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mx-auto w-full max-w-7xl px-4 py-8 sm:px-6 sm:py-10", className)}>
      {children}
    </div>
  );
}
