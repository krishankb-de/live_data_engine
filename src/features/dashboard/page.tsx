import { useState, useEffect } from "react";
import { KpiCard } from "../../components/KpiCard";
import { ListingsTable } from "../../components/ListingsTable";
import { RowModal } from "../../components/RowModal";
import { BusinessCard } from "../../components/BusinessCard";
import { DiffPanel } from "../../components/DiffPanel";
import { AuditLogCard } from "../../components/AuditLogCard";
import { listings as initialListings, seedKpi } from "../../models/fixtures";
import { usePipeline } from "../../hooks/usePipeline";
import { useLivePoll } from "../../hooks/useLivePoll";
import { useHealth, type HealthState } from "../../hooks/useHealth";
import { api, type FieldObs, type VersionOut } from "../../lib/api";
import type { PipelineStep } from "../../hooks/usePipeline";
import type { Listing } from "../../models/types";

const PIPELINE_STEPS: { key: PipelineStep; label: string; desc: string }[] = [
  { key: "detect", label: "Detect", desc: "Check ETag / sitemap for changes" },
  { key: "extract", label: "Extract", desc: "Parse fields from source HTML" },
  { key: "apply", label: "Apply", desc: "Write confident updates to listings" },
];

function ApiStatusDot({ state }: { state: HealthState }) {
  const meta: Record<HealthState, { dot: string; label: string }> = {
    unknown: { dot: "bg-text-muted", label: "API checking…" },
    online: { dot: "bg-success", label: "API online" },
    offline: { dot: "bg-red-500", label: "API offline" },
  };
  const { dot, label } = meta[state];
  return (
    <span
      className="flex items-center gap-1.5 text-xs text-text-muted"
      title={label}
    >
      <span
        className={`inline-block w-2 h-2 rounded-full ${dot}`}
        aria-hidden="true"
      />
      {label}
    </span>
  );
}

function PipelineStepBar({ step }: { step: PipelineStep }) {
  if (step === "idle") return null;
  return (
    <div className="flex items-center gap-0 mt-4 bg-surface rounded-lg shadow-sm px-6 py-3 overflow-hidden">
      {PIPELINE_STEPS.map(({ key, label, desc }, i) => {
        const isActive = step === key;
        const isDone =
          step === "done" ||
          (step === "extract" && key === "detect") ||
          (step === "apply" && (key === "detect" || key === "extract"));
        return (
          <div key={key} className="flex items-center flex-1">
            <div className="flex items-center gap-2.5 flex-1">
              <div
                className={[
                  "w-6 h-6 rounded-full flex items-center justify-center text-xs font-bold shrink-0",
                  isDone
                    ? "bg-success text-white"
                    : isActive
                      ? "bg-primary text-text-inverse"
                      : "bg-surface-muted text-text-muted",
                ].join(" ")}
              >
                {isDone ? "✓" : i + 1}
              </div>
              <div>
                <p
                  className={`text-xs font-semibold ${isActive ? "text-text" : isDone ? "text-success" : "text-text-muted"}`}
                >
                  {label}
                  {isActive && <span className="ml-1 animate-pulse">…</span>}
                </p>
                <p className="text-xs text-text-muted hidden sm:block">
                  {desc}
                </p>
              </div>
            </div>
            {i < PIPELINE_STEPS.length - 1 && (
              <div
                className={`h-px w-6 mx-2 shrink-0 ${isDone ? "bg-success" : "bg-border"}`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

export function DashboardPage() {
  const [selectedListing, setSelectedListing] = useState<Listing | null>(null);
  const {
    listings,
    setListings,
    kpi,
    run,
    accept,
    reject,
    isRunning,
    step,
    fieldUpdatesByListing,
    setFieldUpdatesByListing,
  } = usePipeline(initialListings, seedKpi);
  const { secondsUntilNext } = useLivePoll(listings, setListings, (byListing) =>
    setFieldUpdatesByListing((prev) => ({ ...prev, ...byListing })),
  );
  const healthState = useHealth();
  const [observations, setObservations] = useState<FieldObs[]>([]);
  const [versions, setVersions] = useState<VersionOut[]>([]);
  const [sendingApproval, setSendingApproval] = useState(false);

  useEffect(() => {
    if (!selectedListing) {
      setObservations([]);
      setVersions([]);
      return;
    }
    const numId = Number(selectedListing.id);
    if (!Number.isFinite(numId)) return;
    let cancelled = false;
    void Promise.all([api.getListing(numId), api.getListingVersions(numId)])
      .then(([detail, vers]) => {
        if (cancelled) return;
        setObservations(detail.latest_observations);
        setVersions(vers);
      })
      .catch(() => {
        if (cancelled) return;
        setObservations([]);
        setVersions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedListing]);

  return (
    <>
      {/* Header */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <h1 className="text-2xl font-semibold text-text">Overview</h1>
        <div className="flex flex-wrap items-center gap-3">
          <ApiStatusDot state={healthState} />
          <span className="text-xs text-text-muted">
            Next check in {Math.floor(secondsUntilNext / 60)}:
            {String(secondsUntilNext % 60).padStart(2, "0")}
          </span>
          <button
            type="button"
            disabled={isRunning}
            className="bg-primary text-text-inverse rounded-lg px-4 py-2 text-sm font-medium hover:bg-primary-hover disabled:opacity-60 disabled:cursor-not-allowed"
            onClick={() => {
              void run();
            }}
          >
            {isRunning ? "Running…" : "Run pipeline now"}
          </button>
        </div>
      </div>

      <PipelineStepBar step={step} />

      {/* KPI row */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 mt-6">
        <KpiCard label="Entries Checked" value={kpi.entriesChecked} />
        <KpiCard label="Auto-Updates" value={kpi.autoUpdates} />
        <KpiCard label="Needs Review" value={kpi.needsReview} />
        <KpiCard label="Updates Today" value={kpi.updatesToday} />
      </div>

      {/* Section heading */}
      <h2 className="text-base font-semibold text-text mt-8">Updates Today</h2>

      {/* Table */}
      <div className="mt-4 bg-surface rounded-lg shadow-sm overflow-hidden">
        <ListingsTable
          listings={listings}
          onRowClick={(l) => setSelectedListing(l)}
        />
      </div>

      {/* Count line */}
      <p className="mt-2 text-sm text-text">Total: {listings.length}</p>

      {selectedListing && (
        <RowModal
          open={true}
          onClose={() => setSelectedListing(null)}
          onAccept={
            selectedListing.status === "needs_review" && !selectedListing.emailSent
              ? () => {
                  accept(selectedListing.id);
                  setSelectedListing(null);
                }
              : undefined
          }
          onReject={
            selectedListing.status === "needs_review" && !selectedListing.emailSent
              ? () => {
                  reject(selectedListing.id);
                  setSelectedListing(null);
                }
              : undefined
          }
          emailSentPending={
            selectedListing.status === "needs_review" && selectedListing.emailSent === true
          }
        >
          <div className="p-6 flex flex-col gap-4">
            <BusinessCard
              listing={selectedListing}
              observations={observations}
            />
            <DiffPanel
              updates={fieldUpdatesByListing[selectedListing.id] ?? []}
              versions={versions}
              sendingApproval={sendingApproval}
              onSendApproval={
                (fieldUpdatesByListing[selectedListing.id] ?? []).some(
                  (u) => u.confidence < 0.5,
                )
                  ? () => {
                      const numId = Number(selectedListing.id);
                      if (!Number.isFinite(numId)) {
                        alert("Cannot send: listing id is not numeric.");
                        return;
                      }
                      setSendingApproval(true);
                      void api
                        .sendApprovalEmail(numId)
                        .then((res) => {
                          alert(`Approval email sent to ${res.to}`);
                        })
                        .catch((e: unknown) => {
                          alert(
                            `Failed to send: ${e instanceof Error ? e.message : String(e)}`,
                          );
                        })
                        .finally(() => setSendingApproval(false));
                    }
                  : undefined
              }
            />
            <AuditLogCard
              listing={selectedListing}
              updates={fieldUpdatesByListing[selectedListing.id] ?? []}
              versions={versions}
            />
          </div>
        </RowModal>
      )}
    </>
  );
}
