import { useState, useCallback } from "react";
import type React from "react";
import type { Listing, KpiData, MonitoredField } from "../models/types";
import type { FieldUpdate } from "../lib/pipeline/types";
import { detect } from "../lib/pipeline/detect";
import { extract } from "../lib/pipeline/extract";
import { applyUpdates } from "../lib/pipeline/apply";
import { sourceFixtures } from "../models/fixtures";

export type PipelineStep = "idle" | "detect" | "extract" | "apply" | "done";

interface PipelineResult {
  listings: Listing[];
  setListings: React.Dispatch<React.SetStateAction<Listing[]>>;
  kpi: KpiData;
  run: () => Promise<void>;
  accept: (listingId: string) => void;
  reject: (listingId: string) => void;
  isRunning: boolean;
  step: PipelineStep;
  fieldUpdatesByListing: Record<string, FieldUpdate[]>;
  setFieldUpdatesByListing: React.Dispatch<React.SetStateAction<Record<string, FieldUpdate[]>>>;
}

export function usePipeline(
  initialListings: Listing[],
  initialKpi: KpiData
): PipelineResult {
  const [listings, setListings] = useState<Listing[]>(initialListings);
  const [kpi, setKpi] = useState<KpiData>(initialKpi);
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<PipelineStep>("idle");
  const [fieldUpdatesByListing, setFieldUpdatesByListing] = useState<Record<string, FieldUpdate[]>>({});

  const run = useCallback(async () => {
    setIsRunning(true);

    // Each step has a 400 ms delay so the animated step bar in the UI is visible
    setStep("detect");
    await new Promise<void>((r) => setTimeout(r, 400));
    const detectResults = listings.map((listing) => {
      const fixture = sourceFixtures.find((f) => f.listingId === listing.id);
      return { listing, fixture, detectResult: fixture ? detect(fixture) : null };
    });

    setStep("extract");
    await new Promise<void>((r) => setTimeout(r, 400));
    const extracted = detectResults.map(({ listing, fixture, detectResult }) => {
      if (!fixture || !detectResult) return { listing, updates: [] };
      // Offline listings get status flagged but no field changes attempted
      if (detectResult.signal === "offline") return { listing: { ...listing, status: "source_offline" as const }, updates: [] };
      if (!detectResult.changed) return { listing, updates: [] };
      const { updates } = extract(listing, fixture);
      return { listing, updates };
    });

    setStep("apply");
    await new Promise<void>((r) => setTimeout(r, 400));
    const nextListings = extracted.map(({ listing, updates }) =>
      updates.length > 0 ? applyUpdates(listing, updates) : listing
    );

    // Build index so the modal can look up FieldUpdates (confidence, old/new values) by listing id
    const byListing: Record<string, FieldUpdate[]> = {};
    for (const { listing, updates } of extracted) {
      if (updates.length > 0) byListing[listing.id] = updates;
    }

    const autoUpdates = nextListings.filter((l) => l.status === "auto_applied").length;
    const needsReview = nextListings.filter((l) => l.status === "needs_review").length;
    const updatesToday = nextListings.filter((l) => l.changedFields.length > 0).length;
    const entriesChecked = nextListings.length;

    setListings(nextListings);
    setFieldUpdatesByListing(byListing);
    setKpi({ autoUpdates, needsReview, updatesToday, entriesChecked });
    setStep("done");
    setIsRunning(false);
  }, [listings]);

  // Accept only flips the status badge — applyUpdates already ran and the new value
  // was NOT written (needs_review skips field writes). A real system would apply newValue here.
  const accept = useCallback((listingId: string) => {
    setListings((prev) =>
      prev.map((l) => l.id === listingId ? { ...l, status: "auto_applied" as const } : l)
    );
    setKpi((prev) => ({
      ...prev,
      autoUpdates: prev.autoUpdates + 1,
      needsReview: Math.max(0, prev.needsReview - 1),
    }));
  }, []);

  const reject = useCallback((listingId: string) => {
    setListings((prev) =>
      prev.map((l) => {
        if (l.id !== listingId) return l;
        // Restore each field to its oldValue from the stored FieldUpdate objects
        const updates = fieldUpdatesByListing[listingId] ?? [];
        const reverted = { ...l, changedFields: [] as MonitoredField[], status: "auto_applied" as const };
        for (const u of updates) {
          (reverted as Record<string, unknown>)[u.field] = u.oldValue;
        }
        return reverted;
      })
    );
    setKpi((prev) => ({
      ...prev,
      needsReview: Math.max(0, prev.needsReview - 1),
      updatesToday: Math.max(0, prev.updatesToday - 1),
    }));
    // Clear the stored updates so the diff panel shows clean on next open
    setFieldUpdatesByListing((prev) => {
      const next = { ...prev };
      delete next[listingId];
      return next;
    });
  }, [fieldUpdatesByListing]);

  return { listings, setListings, kpi, run, accept, reject, isRunning, step, fieldUpdatesByListing, setFieldUpdatesByListing };
}
