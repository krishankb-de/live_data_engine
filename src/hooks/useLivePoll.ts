import { useEffect, useRef, useState, useCallback } from "react";
import type { Listing } from "../models/types";
import type { FieldUpdate, SourceFixture } from "../lib/pipeline/types";
import { detect } from "../lib/pipeline/detect";
import { extract } from "../lib/pipeline/extract";
import { applyUpdates } from "../lib/pipeline/apply";

const POLL_INTERVAL = 30; // seconds between each source-site check

interface LivePollResult {
  secondsUntilNext: number;
  lastEtag: string | null;
}

export function useLivePoll(
  liveListings: Listing[],
  onUpdate: (updated: Listing[]) => void,
  onFieldUpdates?: (byListing: Record<string, FieldUpdate[]>) => void
): LivePollResult {
  const [secondsUntilNext, setSecondsUntilNext] = useState(POLL_INTERVAL);
  const etagRef = useRef<string | null>(null);
  // Refs instead of deps so the poll closure always reads the latest values
  // without being recreated — recreation would restart the interval and reset the countdown
  const listingsRef = useRef<Listing[]>(liveListings);
  listingsRef.current = liveListings;
  const onFieldUpdatesRef = useRef(onFieldUpdates);
  onFieldUpdatesRef.current = onFieldUpdates;

  const poll = useCallback(async () => {
    // Only one listing is marked live (the demo source site)
    const listing = listingsRef.current.find((l) => l.live);
    if (!listing) return;

    // Conditional GET: server returns 304 if ETag hasn't changed, saving a full parse
    const headers: Record<string, string> = {};
    if (etagRef.current) headers["If-None-Match"] = etagRef.current;

    let res: Response;
    try {
      res = await fetch(listing.website + "/business.json", { headers });
    } catch {
      return; // network failure — skip this tick, try again next interval
    }

    if (res.status === 304) return; // source unchanged
    if (!res.ok) return;

    const newEtag = res.headers.get("ETag") ?? "";
    const data = await res.json() as Record<string, string>;

    // Wrap the raw JSON into the HTML fixture format that extract() expects
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

    const updated = applyUpdates(listing, updates);
    onUpdate(listingsRef.current.map((l) => (l.id === updated.id ? updated : l)));
    // Also push the FieldUpdate objects so the modal's DiffPanel can show confidence scores
    onFieldUpdatesRef.current?.({ [listing.id]: updates });
  }, [onUpdate]);

  useEffect(() => {
    void poll();

    setSecondsUntilNext(POLL_INTERVAL);
    const countdownId = setInterval(() => {
      setSecondsUntilNext((s) => {
        if (s <= 1) {
          void poll();
          return POLL_INTERVAL;
        }
        return s - 1;
      });
    }, 1000);

    return () => clearInterval(countdownId);
  }, [poll]);

  return { secondsUntilNext, lastEtag: etagRef.current };
}
