import {
  ArrowRight,
  BadgeCheck,
  FileSearch,
  Flag,
  Gauge,
  LayoutDashboard,
  Network,
  ScanSearch,
  ShieldCheck,
} from "lucide-react";
import { ButtonLink } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

/** The six steps the product performs, in pipeline order. */
const STEPS = [
  {
    icon: ScanSearch,
    title: "Submit an investment claim",
    body: "Paste the message, post or promotion you received. The investigation pipeline takes it from there.",
  },
  {
    icon: FileSearch,
    title: "Extract claims and entities",
    body: "Every checkable statement is pulled out, along with the people, organisations, regulators, platforms and registration numbers involved.",
  },
  {
    icon: Flag,
    title: "Detect red-flag patterns",
    body: "Fifteen deterministic rules identify pressure tactics, guaranteed returns, regulatory claims and payment red flags in the content itself.",
  },
  {
    icon: Network,
    title: "Verify against evidence",
    body: "Each claim is checked against regulator, government and exchange records, and every result is kept as a traceable source.",
  },
  {
    icon: Gauge,
    title: "Assess transparent risk indicators",
    body: "A heuristic score is computed from the factors found, with its weights, contributions and thresholds all visible.",
  },
  {
    icon: BadgeCheck,
    title: "Understand why it was flagged",
    body: "The report links each flag to the exact text that triggered it and to the evidence that supports or contradicts the claim.",
  },
] as const;

/** The single product promise. Kept as a constant so it is stated identically. */
const TAGLINE = "Investigate Before You Invest.";

const PRODUCT_STATEMENT =
  "InvestShield does not just detect suspicious investment content. It investigates the claims behind it and shows the evidence.";

export function LandingPage() {
  return (
    <div>
      {/* Hero */}
      <section className="relative overflow-hidden border-b border-hairline">
        <div aria-hidden="true" className="grid-substrate absolute inset-0" />
        <div className="relative mx-auto max-w-7xl px-4 py-16 sm:px-6 sm:py-24">
          <div className="max-w-3xl">
            <p className="inline-flex items-center gap-2 rounded-full border border-hairline bg-surface-raised px-3 py-1 text-xs font-medium text-ink-muted">
              <ShieldCheck aria-hidden="true" className="size-3.5 text-accent" />
              AI-powered investment claim investigation
            </p>

            <h1 className="mt-5 text-4xl font-semibold tracking-tight text-balance text-ink sm:text-5xl lg:text-6xl">
              {TAGLINE}
            </h1>

            <p className="mt-5 max-w-2xl text-lg leading-relaxed text-ink-muted">
              {PRODUCT_STATEMENT}
            </p>

            <div className="mt-8 flex flex-wrap gap-3">
              <ButtonLink to="/investigate" size="lg">
                Start an Investigation
                <ArrowRight aria-hidden="true" />
              </ButtonLink>
              <ButtonLink to="/dashboard" size="lg" variant="outline">
                View Dashboard
              </ButtonLink>
            </div>

            <p className="mt-6 max-w-2xl text-sm leading-relaxed text-ink-faint">
              InvestShield reports evidence and risk indicators. It is not financial advice, it
              does not recommend investments, and it does not guarantee that any particular
              investment is safe or fraudulent.
            </p>
          </div>
        </div>
      </section>

      {/* What it does */}
      <section className="mx-auto max-w-7xl px-4 py-14 sm:px-6 sm:py-20" aria-labelledby="how-heading">
        <div className="max-w-2xl">
          <h2 id="how-heading" className="text-2xl font-semibold tracking-tight text-ink">
            An investigation, not a label
          </h2>
          <p className="mt-3 leading-relaxed text-ink-muted">
            Suspicious-looking text is only the starting point. The substance of the question is
            whether the claims inside it hold up — so that is what gets investigated, and every
            step is traceable back to the source text and the records consulted.
          </p>
        </div>

        <ol className="mt-10 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((step, index) => {
            const Icon = step.icon;
            return (
              <li key={step.title}>
                <Card className="h-full">
                  <CardContent className="space-y-3 p-5">
                    <div className="flex items-center gap-2.5">
                      <span className="flex size-8 shrink-0 items-center justify-center rounded bg-surface-overlay text-accent">
                        <Icon aria-hidden="true" className="size-4" />
                      </span>
                      <span className="font-mono text-xs text-ink-faint">
                        {String(index + 1).padStart(2, "0")}
                      </span>
                    </div>
                    <h3 className="text-sm font-semibold text-ink">{step.title}</h3>
                    <p className="text-sm leading-relaxed text-ink-muted">{step.body}</p>
                  </CardContent>
                </Card>
              </li>
            );
          })}
        </ol>
      </section>

      {/* The investigation flow */}
      <section
        className="border-y border-hairline bg-surface-raised/40"
        aria-labelledby="flow-heading"
      >
        <div className="mx-auto max-w-7xl px-4 py-14 sm:px-6 sm:py-20">
          <h2 id="flow-heading" className="text-2xl font-semibold tracking-tight text-ink">
            What a report shows you
          </h2>
          <p className="mt-3 max-w-2xl leading-relaxed text-ink-muted">
            Every investigation walks the same path, and the report is organised along it.
          </p>

          <ol className="mt-8 flex flex-wrap items-center gap-2 text-sm">
            {[
              "Input",
              "Claims",
              "Entities",
              "Red Flags",
              "Verification",
              "Evidence",
              "Risk",
              "Report",
            ].map((step, index, all) => (
              <li key={step} className="flex items-center gap-2">
                <span className="rounded border border-hairline bg-surface px-3 py-1.5 font-medium text-ink">
                  {step}
                </span>
                {index < all.length - 1 ? (
                  <ArrowRight aria-hidden="true" className="size-3.5 text-ink-faint" />
                ) : null}
              </li>
            ))}
          </ol>

          <div className="mt-10 grid gap-4 lg:grid-cols-3">
            <Card>
              <CardContent className="space-y-2 p-5">
                <h3 className="text-sm font-semibold text-ink">Evidence over assertion</h3>
                <p className="text-sm leading-relaxed text-ink-muted">
                  Claims are checked against regulator, government and exchange records. Every
                  excerpt keeps its source URL, retrieval time and relationship to the claim.
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="space-y-2 p-5">
                <h3 className="text-sm font-semibold text-ink">Honest about gaps</h3>
                <p className="text-sm leading-relaxed text-ink-muted">
                  When a claim could not be checked, the report says so and explains why. An
                  unverified claim is never presented as a fraudulent one.
                </p>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="space-y-2 p-5">
                <h3 className="text-sm font-semibold text-ink">Explainable scoring</h3>
                <p className="text-sm leading-relaxed text-ink-muted">
                  Risk indicators are a transparent heuristic. The factors, weights, contributions
                  and thresholds behind the score are all shown.
                </p>
              </CardContent>
            </Card>
          </div>
        </div>
      </section>

      {/* Entry points */}
      <section className="mx-auto max-w-7xl px-4 py-14 sm:px-6 sm:py-20" aria-labelledby="start-heading">
        <div className="grid gap-4 sm:grid-cols-2">
          <Card>
            <CardContent className="space-y-3 p-6">
              <FileSearch aria-hidden="true" className="size-5 text-accent" />
              <h3 className="text-base font-semibold text-ink">Start an Investigation</h3>
              <p className="text-sm leading-relaxed text-ink-muted">
                Submit the investment content you want examined and follow the pipeline stage by
                stage.
              </p>
              <ButtonLink to="/investigate" variant="outline" size="sm">
                Go to investigation
                <ArrowRight aria-hidden="true" />
              </ButtonLink>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="space-y-3 p-6">
              <LayoutDashboard aria-hidden="true" className="size-5 text-accent" />
              <h3 className="text-base font-semibold text-ink">View Dashboard</h3>
              <p className="text-sm leading-relaxed text-ink-muted">
                Review stored investigations, system status and the state of the investigation
                workspace.
              </p>
              <ButtonLink to="/dashboard" variant="outline" size="sm">
                Go to dashboard
                <ArrowRight aria-hidden="true" />
              </ButtonLink>
            </CardContent>
          </Card>
        </div>
      </section>
    </div>
  );
}
