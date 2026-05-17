import { useEffect, useRef, useState, useCallback } from "react";
import type { Listing } from "../models/types";
import type { FieldUpdate, SourceFixture } from "../lib/pipeline/types";
import { detect } from "../lib/pipeline/detect";
import { extract } from "../lib/pipeline/extract";
import { applyUpdates } from "../lib/pipeline/apply";
import { api } from "../lib/api";

const POLL_INTERVAL = 30; // seconds

interface LivePollResult {
  secondsUntilNext: number;
  lastEtag: string | null;
}

export function useLivePoll(
  liveListings: Listing[],
  onUpdate: (updated: Listing[]) => void,
  onFieldUpdates?: (byListing: Record<string, FieldUpdate[]>) => void,
): LivePollResult {
  const [secondsUntilNext, setSecondsUntilNext] = useState(POLL_INTERVAL);
  const etagRef = useRef<string | null>(null);
  const listingsRef = useRef<Listing[]>(liveListings);
  listingsRef.current = liveListings;
  const onFieldUpdatesRef = useRef(onFieldUpdates);
  onFieldUpdatesRef.current = onFieldUpdates;

  const poll = useCallback(async () => {
    const listing = listingsRef.current.find((l) => l.live);
    if (!listing) return;

    const headers: Record<string, string> = {};
    if (etagRef.current) headers["If-None-Match"] = etagRef.current;

    let res: Response;
    try {
      res = await fetch(listing.website + "/business.json", { headers });
    } catch {
      return;
    }

    if (res.status === 304) return;
    if (!res.ok) return;

    const newEtag = res.headers.get("ETag") ?? "";
    const data = (await res.json()) as Record<string, string>;

    const fixture: SourceFixture = {
      listingId: listing.id,
      sitemap_lastmod_changed: false,
      etag_old: etagRef.current ?? "",
      etag_new: newEtag,
      last_modified_old: "",
      last_modified_new: "",
      source_html: `<p data-field="phone">${data.phone}</p><p data-field="opening_hours">${data.opening_hours}</p>`,
    };

    etagRef.current = newEtag;

    const detectResult = detect(fixture);
    if (!detectResult.changed) return;

    const { updates } = extract(listing, fixture);
    if (updates.length === 0) return;

    // Apply changes locally for instant UI feedback
    const updated = applyUpdates(listing, updates);
    onUpdate(listingsRef.current.map((l) => (l.id === updated.id ? updated : l)));
    onFieldUpdatesRef.current?.({ [listing.id]: updates });

    // Also trigger a backend batch for phases that re-extract + hash the changed record
    void api.postBatch([3, 6]).catch(() => {
      // Backend unavailable — local update already applied above, nothing more to do
    });
  }, [onUpdate]);

  useEffect(() => {
    void poll();
    setSecondsUntilNext(POLL_INTERVAL);
    const id = setInterval(() => {
      setSecondsUntilNext((s) => {
        if (s <= 1) {
          void poll();
          return POLL_INTERVAL;
        }
        return s - 1;
      });
    }, 1000);
    return () => clearInterval(id);
  }, [poll]);

  return { secondsUntilNext, lastEtag: etagRef.current };
}
