import { useState, useCallback, useEffect, useRef } from "react";
import type React from "react";
import type { Listing, KpiData, MonitoredField } from "../models/types";
import type { FieldUpdate } from "../lib/pipeline/types";
import { api, pollBatch } from "../lib/api";
import type { ListingOut, VersionOut } from "../lib/api";
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

function mapListingOut(row: ListingOut, pendingVersions: VersionOut[]): Listing {
  const mine = pendingVersions.filter((v) => v.listing_id === row.id);
  const hasReview = mine.length > 0;
  return {
    id: String(row.id),
    name: row.name,
    category: row.category ?? "",
    tier: "A",
    address: row.address ?? "",
    phone: row.phone ?? "",
    email: "",
    website: row.website_url ?? "",
    opening_hours: row.opening_hours ?? "",
    lastRunAt: row.last_checked ?? row.updated_at ?? null,
    status: hasReview ? "needs_review" : null,
    confidence: hasReview ? (mine[0].intent_confidence ?? null) : null,
    changedFields: [...new Set(mine.map((v) => v.field))] as MonitoredField[],
    emailSent: false,
  };
}


export function usePipeline(
  initialListings: Listing[],
  initialKpi: KpiData,
): PipelineResult {
  const [listings, setListings] = useState<Listing[]>(initialListings);
  const [kpi, setKpi] = useState<KpiData>(initialKpi);
  const [isRunning, setIsRunning] = useState(false);
  const [step, setStep] = useState<PipelineStep>("idle");
  const [fieldUpdatesByListing, setFieldUpdatesByListing] = useState<Record<string, FieldUpdate[]>>({});

  // Stores pending versions from the DB; keyed on numeric listing_id for O(1) lookup
  const pendingVersionsRef = useRef<VersionOut[]>([]);
  // null = not yet determined, true = reachable, false = unreachable
  const apiAvailableRef = useRef<boolean | null>(null);

  // ---------------------------------------------------------------------------
  // Fetch all from API and sync state
  // ---------------------------------------------------------------------------
  const fetchAll = useCallback(async () => {
    try {
      const [listingsResp, reviewsResp] = await Promise.all([
        api.getListings({ limit: 200 }),
        api.getPendingReviews({ limit: 200 }),
      ]);
      apiAvailableRef.current = true;

      const pending = reviewsResp.items;
      pendingVersionsRef.current = pending;

      const mapped = listingsResp.items.map((row) => mapListingOut(row, pending));
      setListings(mapped);

      // Build FieldUpdate map for the DiffPanel
      const byListing: Record<string, FieldUpdate[]> = {};
      for (const v of pending) {
        const key = String(v.listing_id);
        (byListing[key] ??= []).push({
          listingId: key,
          field: v.field as MonitoredField,
          oldValue: v.old_value ?? "",
          newValue: v.new_value ?? "",
          confidence: v.intent_confidence ?? 0.5,
          status: "needs_review" as const,
        });
      }
      setFieldUpdatesByListing(byListing);

      const autoUpdates = mapped.filter((l) => l.status === "auto_applied").length;
      const needsReview = mapped.filter((l) => l.status === "needs_review").length;
      setKpi({
        entriesChecked: listingsResp.total,
        autoUpdates,
        needsReview,
        updatesToday: autoUpdates + needsReview,
      });
    } catch {
      // Backend unreachable — keep fixture state
      apiAvailableRef.current = false;
    }
  }, []);

  // Attempt to load real data on mount
  useEffect(() => {
    void fetchAll();
  }, [fetchAll]);

  // ---------------------------------------------------------------------------
  // run() — trigger backend pipeline or fall back to local fixture simulation
  // ---------------------------------------------------------------------------
  const run = useCallback(async () => {
    setIsRunning(true);

    if (!apiAvailableRef.current) {
      // ---- Local fixture simulation (offline fallback) ----
      setStep("detect");
      await new Promise<void>((r) => setTimeout(r, 400));
      const detectResults = listings.map((listing) => {
        const fixture = sourceFixtures.find((f) => f.listingId === listing.id);
        return { listing, fixture, detectResult: fixture ? detect(fixture) : null };
      });

      setStep("extract");
      await new Promise<void>((r) => setTimeout(r, 400));
      const extracted = detectResults.map(({ listing, fixture, detectResult }) => {
        if (!fixture || !detectResult) return { listing, updates: [] as FieldUpdate[] };
        if (detectResult.signal === "offline")
          return { listing: { ...listing, status: "source_offline" as const }, updates: [] as FieldUpdate[] };
        if (!detectResult.changed) return { listing, updates: [] as FieldUpdate[] };
        const { updates } = extract(listing, fixture);
        return { listing, updates };
      });

      setStep("apply");
      await new Promise<void>((r) => setTimeout(r, 400));
      const nextListings = extracted.map(({ listing, updates }) =>
        updates.length > 0 ? applyUpdates(listing, updates) : listing,
      );
      const byListing: Record<string, FieldUpdate[]> = {};
      for (const { listing, updates } of extracted) {
        if (updates.length > 0) byListing[listing.id] = updates;
      }
      const autoUpdates = nextListings.filter((l) => l.status === "auto_applied").length;
      const needsReview = nextListings.filter((l) => l.status === "needs_review").length;
      setListings(nextListings);
      setFieldUpdatesByListing(byListing);
      setKpi({ autoUpdates, needsReview, updatesToday: autoUpdates + needsReview, entriesChecked: nextListings.length });
      setStep("done");
      setIsRunning(false);
      return;
    }

    // ---- Backend pipeline ----
    try {
      setStep("detect");
      const { batch_id } = await api.postBatch([1, 2, 3, 4, 6]);

      setStep("extract");
      const final = await pollBatch(batch_id, (s) => {
        if (s.status === "running") setStep("apply");
      });

      setStep("done");
      await fetchAll();

      if (final.listings_processed > 0) {
        setKpi((prev) => ({
          ...prev,
          entriesChecked: final.listings_processed,
          autoUpdates: final.changes_auto_applied,
          needsReview: final.changes_review_queue,
          updatesToday: final.changes_auto_applied + final.changes_review_queue,
        }));
      }
    } catch (e) {
      console.error("Pipeline run failed:", e);
      setStep("idle");
    } finally {
      setIsRunning(false);
    }
  }, [listings, fetchAll]);

  // ---------------------------------------------------------------------------
  // accept / reject
  // ---------------------------------------------------------------------------
  const accept = useCallback(
    (listingId: string) => {
      // Optimistic update
      setListings((prev) =>
        prev.map((l) => (l.id === listingId ? { ...l, status: "auto_applied" as const } : l)),
      );
      setKpi((prev) => ({
        ...prev,
        autoUpdates: prev.autoUpdates + 1,
        needsReview: Math.max(0, prev.needsReview - 1),
      }));

      if (!apiAvailableRef.current) return;

      const numId = parseInt(listingId, 10);
      const vers = pendingVersionsRef.current.filter((v) => v.listing_id === numId);
      void Promise.all(vers.map((v) => api.acceptVersion(v.id))).then(() => fetchAll());
    },
    [fetchAll],
  );

  const reject = useCallback(
    (listingId: string) => {
      // Optimistic update: revert fields to oldValue and clear review state
      setListings((prev) =>
        prev.map((l) => {
          if (l.id !== listingId) return l;
          const updates = fieldUpdatesByListing[listingId] ?? [];
          const reverted: Listing = { ...l, changedFields: [], status: null };
          for (const u of updates) {
            (reverted as unknown as Record<string, unknown>)[u.field] = u.oldValue;
          }
          return reverted;
        }),
      );
      setKpi((prev) => ({
        ...prev,
        needsReview: Math.max(0, prev.needsReview - 1),
        updatesToday: Math.max(0, prev.updatesToday - 1),
      }));
      setFieldUpdatesByListing((prev) => {
        const next = { ...prev };
        delete next[listingId];
        return next;
      });

      if (!apiAvailableRef.current) return;

      const numId = parseInt(listingId, 10);
      const vers = pendingVersionsRef.current.filter((v) => v.listing_id === numId);
      void Promise.all(vers.map((v) => api.rejectVersion(v.id))).then(() => fetchAll());
    },
    [fetchAll, fieldUpdatesByListing],
  );

  return {
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
  };
}
