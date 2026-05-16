import { useState } from "react";
import { Sidebar } from "../../components/Sidebar";
import { KpiCard } from "../../components/KpiCard";
import { FilterPills } from "../../components/FilterPills";
import { ListingsTable } from "../../components/ListingsTable";
import { RowModal } from "../../components/RowModal";
import { BusinessCard } from "../../components/BusinessCard";
import { DiffPanel } from "../../components/DiffPanel";
import { AuditLogCard } from "../../components/AuditLogCard";
import { listings as initialListings, seedKpi } from "../../models/fixtures";
import { usePipeline } from "../../hooks/usePipeline";
import { useLivePoll } from "../../hooks/useLivePoll";
import type { PipelineStep } from "../../hooks/usePipeline";
import type { Listing } from "../../models/types";

type FilterOption = "All" | "Auto-Update" | "Needs Review" | "Email Sent" | "Website Offline";

const PIPELINE_STEPS: { key: PipelineStep; label: string; desc: string }[] = [
  { key: "detect",  label: "Detect",  desc: "Check ETag / sitemap for changes" },
  { key: "extract", label: "Extract", desc: "Parse fields from source HTML" },
  { key: "apply",   label: "Apply",   desc: "Write confident updates to listings" },
];

function PipelineStepBar({ step }: { step: PipelineStep }) {
  if (step === "idle") return null;
  return (
    <div className="flex items-center gap-0 mt-4 bg-surface rounded-lg shadow-sm px-6 py-3 overflow-hidden">
      {PIPELINE_STEPS.map(({ key, label, desc }, i) => {
        const isActive = step === key;
        const isDone = step === "done" ||
          (step === "extract" && key === "detect") ||
          (step === "apply" && (key === "detect" || key === "extract"));
        return (
          <div key={key} className="flex items-center flex-1">
            <div className="flex items-center gap-2.5 flex-1">
              <div className={[
                "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0",
                isDone ? "bg-success text-white" : isActive ? "bg-primary text-text-inverse" : "bg-surface-muted text-text-muted",
              ].join(" ")}>
                {isDone ? "✓" : i + 1}
              </div>
              <div>
                <p className={`text-xs font-semibold ${isActive ? "text-text" : isDone ? "text-success" : "text-text-muted"}`}>
                  {label}
                  {isActive && <span className="ml-1 animate-pulse">…</span>}
                </p>
                <p className="text-xs text-text-muted hidden sm:block">{desc}</p>
              </div>
            </div>
            {i < PIPELINE_STEPS.length - 1 && (
              <div className={`h-px w-6 mx-2 shrink-0 ${isDone ? "bg-success" : "bg-border"}`} />
            )}
          </div>
        );
      })}
    </div>
  );
}

function matchesFilter(listing: Listing, filter: FilterOption): boolean {
  if (filter === "All") return true;
  if (filter === "Auto-Update") return listing.status === "auto_applied";
  if (filter === "Needs Review") return listing.status === "needs_review";
  if (filter === "Email Sent") return listing.emailSent === true;
  if (filter === "Website Offline") return listing.status === "source_offline";
  return true;
}

export function DashboardPage() {
  const [activeFilter, setActiveFilter] = useState<FilterOption>("All");
  const [selectedListing, setSelectedListing] = useState<Listing | null>(null);
  const { listings, setListings, kpi, run, accept, reject, isRunning, step, fieldUpdatesByListing, setFieldUpdatesByListing } = usePipeline(initialListings, seedKpi);
  const { secondsUntilNext } = useLivePoll(listings, setListings, (byListing) =>
    setFieldUpdatesByListing((prev) => ({ ...prev, ...byListing }))
  );

  const filtered = listings.filter((l) => matchesFilter(l, activeFilter));

  return (
    <div className="flex h-full">
      <Sidebar activeRoute="overview" />

      <div className="flex-1 overflow-y-auto bg-bg p-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <h1 className="text-2xl font-semibold text-text">Overview</h1>
          <div className="flex items-center gap-3">
            <span className="text-xs text-text-muted">
              Next check in {Math.floor(secondsUntilNext / 60)}:{String(secondsUntilNext % 60).padStart(2, "0")}
            </span>
            <button
              type="button"
              disabled={isRunning}
              className="bg-primary text-text-inverse rounded-lg px-4 py-2 text-sm font-medium hover:bg-primary-hover disabled:opacity-60 disabled:cursor-not-allowed"
              onClick={() => { void run(); }}
            >
              {isRunning ? "Running…" : "Run pipeline now"}
            </button>
          </div>
        </div>

        <PipelineStepBar step={step} />

        {/* KPI row */}
        <div className="grid grid-cols-4 gap-6 mt-6">
          <KpiCard label="Entries Checked" value={kpi.entriesChecked} />
          <KpiCard label="Auto-Updates" value={kpi.autoUpdates} />
          <KpiCard label="Needs Review" value={kpi.needsReview} />
          <KpiCard label="Updates Today" value={kpi.updatesToday} />
        </div>

        {/* Section heading + filter pills */}
        <div className="flex items-center justify-between mt-8">
          <h2 className="text-base font-semibold text-text">Updates Today</h2>
          <FilterPills active={activeFilter} onSelect={setActiveFilter} />
        </div>

        {/* Table */}
        <div className="mt-4 bg-surface rounded-lg shadow-sm overflow-hidden">
          <ListingsTable listings={filtered} onRowClick={(l) => setSelectedListing(l)} />
        </div>

        {/* Count line */}
        <p className="mt-2 text-sm text-text">
          Showing {filtered.length} of {listings.length}
        </p>
      </div>

      {selectedListing && (
        <RowModal
          open={true}
          onClose={() => setSelectedListing(null)}
          onAccept={selectedListing.status === "needs_review" ? () => { accept(selectedListing.id); setSelectedListing(null); } : undefined}
          onReject={selectedListing.status === "needs_review" ? () => { reject(selectedListing.id); setSelectedListing(null); } : undefined}
        >
          <div className="p-6 flex flex-col gap-4">
            <BusinessCard listing={selectedListing} />
            <DiffPanel updates={fieldUpdatesByListing[selectedListing.id] ?? []} />
            <AuditLogCard listing={selectedListing} updates={fieldUpdatesByListing[selectedListing.id] ?? []} />
          </div>
        </RowModal>
      )}
    </div>
  );
}
