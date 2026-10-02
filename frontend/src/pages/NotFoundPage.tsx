import { Compass } from "lucide-react";
import { PageContainer } from "@/components/layout/app-shell";
import { ButtonLink } from "@/components/ui/button";
import { EmptyState } from "@/components/common/state-blocks";

export function NotFoundPage() {
  return (
    <PageContainer>
      <div className="mx-auto max-w-xl py-10">
        <EmptyState
          icon={<Compass aria-hidden="true" className="size-8" />}
          title="Page not found"
          body="That address does not match any page in InvestShield. It may have been mistyped, or the investigation id may be incomplete."
          action={
            <div className="flex flex-wrap justify-center gap-2">
              <ButtonLink to="/dashboard" size="sm">
                Go to dashboard
              </ButtonLink>
              <ButtonLink to="/" size="sm" variant="outline">
                Back to home
              </ButtonLink>
            </div>
          }
        />
      </div>
    </PageContainer>
  );
}
