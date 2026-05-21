import { BrowserRouter, Routes, Route, Navigate } from "react-router";
import { Layout } from "@/components/Layout";
import { LandingPage } from "@/features/landing";
import { DashboardPage } from "@/features/dashboard";
import { BatchesPage } from "@/features/batches";
import { CostsPage } from "@/features/costs";
import { ListingDetailPage } from "@/features/listing-detail";
import { EmailLogPage } from "@/features/email-log";
import { ManualReviewPage } from "@/features/manual-review";
import { SettingsPage } from "@/features/settings";

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/listing/:id" element={<ListingDetailPage />} />
        <Route element={<Layout />}>
          <Route path="/dashboard" element={<DashboardPage />} />
          <Route path="/pipeline-status" element={<BatchesPage />} />
          <Route path="/costs" element={<CostsPage />} />
          <Route path="/email-log" element={<EmailLogPage />} />
          <Route path="/manual-review" element={<ManualReviewPage />} />
          <Route path="/settings" element={<SettingsPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
