import { BrowserRouter, Routes, Route, Navigate } from "react-router";
import { Layout } from "@/components/Layout";
import { LandingPage } from "@/features/landing";
import { DashboardPage } from "@/features/dashboard";
import { BatchesPage } from "@/features/batches";
import { CostsPage } from "@/features/costs";
import { ListingDetailPage } from "@/features/listing-detail";

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
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  );
}
