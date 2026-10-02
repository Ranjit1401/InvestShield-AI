import { Route, Routes } from "react-router-dom";
import { AppShell } from "@/components/layout/app-shell";
import { DashboardPage } from "@/pages/DashboardPage";
import { HistoryPage } from "@/pages/HistoryPage";
import { InvestigatePage } from "@/pages/InvestigatePage";
import { InvestigationResultPage } from "@/pages/InvestigationResultPage";
import { LandingPage } from "@/pages/LandingPage";
import { NotFoundPage } from "@/pages/NotFoundPage";

/**
 * Route table.
 *
 * The layout wraps every route so navigation and the API status pill are
 * consistent. `*` resolves to the not-found page rather than a blank screen.
 */
export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<LandingPage />} />
        <Route path="/dashboard" element={<DashboardPage />} />
        <Route path="/investigate" element={<InvestigatePage />} />
        <Route path="/investigation/:id" element={<InvestigationResultPage />} />
        <Route path="/history" element={<HistoryPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
