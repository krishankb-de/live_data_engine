import { useState, useEffect } from "react";
import { listings as fixtureListings } from "../../models/fixtures";
import { api } from "../../lib/api";
import type { Listing } from "../../models/types";
import type { VersionOut } from "../../lib/api";
import { ConfidenceBar } from "../../components/ConfidenceBar";

type ReviewStatus = "pending" | "accepted" | "rejected";

interface PendingEntry {
  listing: Listing;
  versions: VersionOut[];
  reviewStatus: ReviewStatus;
}

export function EmailLogPage() {
  const [entries, setEntries] = useState<PendingEntry[]>(() =>
    fixtureListings
      .filter((l) => l.emailSent && l.status === "needs_review")
      .map((l) => ({ listing: l, versions: [], reviewStatus: "pending" as ReviewStatus })),
  );
  const [apiOnline, setApiOnline] = useState(false);
  const [processing, setProcessing] = useState<Set<string>>(new Set());

  useEffect(() => {
    void api
      .getPendingReviews({ limit: 200 })
      .then((res) => {
        setApiOnline(true);
        // Group versions by listing_id
        const byListing = new Map<number, VersionOut[]>();
        for (const v of res.items) {
          const arr = byListing.get(v.listing_id) ?? [];
          arr.push(v);
          byListing.set(v.listing_id, arr);
        }
        // Attach real version data to entries where listing id matches
        setEntries((prev) =>
          prev.map((e) => {
            const numId = Number(e.listing.id);
            const vers = byListing.get(numId);
            return vers ? { ...e, versions: vers } : e;
          }),
        );
      })
      .catch(() => {});
  }, []);

  function markProcessing(id: string, on: boolean) {
    setProcessing((prev) => {
      const next = new Set(prev);
      on ? next.add(id) : next.delete(id);
      return next;
    });
  }

  async function handleAccept(entry: PendingEntry) {
    const id = entry.listing.id;
    markProcessing(id, true);
    try {
      if (apiOnline && entry.versions.length > 0) {
        await Promise.all(entry.versions.map((v) => api.acceptVersion(v.id)));
      }
      setEntries((prev) =>
        prev.map((e) => (e.listing.id === id ? { ...e, reviewStatus: "accepted" } : e)),
      );
    } catch {
      alert("Failed to accept — please try again.");
    } finally {
      markProcessing(id, false);
    }
  }

  async function handleReject(entry: PendingEntry) {
    const id = entry.listing.id;
    markProcessing(id, true);
    try {
      if (apiOnline && entry.versions.length > 0) {
        await Promise.all(entry.versions.map((v) => api.rejectVersion(v.id)));
      }
      setEntries((prev) =>
        prev.map((e) => (e.listing.id === id ? { ...e, reviewStatus: "rejected" } : e)),
      );
    } catch {
      alert("Failed to reject — please try again.");
    } finally {
      markProcessing(id, false);
    }
  }

  const pending = entries.filter((e) => e.reviewStatus === "pending");
  const resolved = entries.filter((e) => e.reviewStatus !== "pending");

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-text">Email Log</h1>
        <span className="text-xs text-text-muted">
          {pending.length} pending · {resolved.length} resolved
        </span>
      </div>

      {pending.length === 0 && resolved.length === 0 && (
        <div className="bg-surface rounded-lg shadow-sm p-8 text-center text-text-muted text-sm">
          No email approval requests pending.
        </div>
      )}

      {pending.length > 0 && (
        <div className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
            Awaiting business response
          </h2>
          {pending.map((entry) => (
            <EmailReviewCard
              key={entry.listing.id}
              entry={entry}
              busy={processing.has(entry.listing.id)}
              onAccept={() => void handleAccept(entry)}
              onReject={() => void handleReject(entry)}
            />
          ))}
        </div>
      )}

      {resolved.length > 0 && (
        <div className="flex flex-col gap-3 mt-4">
          <h2 className="text-sm font-semibold uppercase tracking-wide text-text-muted">
            Resolved
          </h2>
          {resolved.map((entry) => (
            <EmailReviewCard
              key={entry.listing.id}
              entry={entry}
              busy={false}
              resolved
            />
          ))}
        </div>
      )}
    </div>
  );
}

interface CardProps {
  entry: PendingEntry;
  busy: boolean;
  resolved?: boolean;
  onAccept?: () => void;
  onReject?: () => void;
}

function EmailReviewCard({ entry, busy, resolved, onAccept, onReject }: CardProps) {
  const { listing } = entry;
  const isAccepted = entry.reviewStatus === "accepted";
  const isRejected = entry.reviewStatus === "rejected";

  // Build changed-field rows: prefer real API version data, fall back to fixture changedFields
  const fieldRows: { field: string; oldVal: string; newVal: string }[] =
    entry.versions.length > 0
      ? entry.versions.map((v) => ({
          field: v.field,
          oldVal: v.old_value ?? "—",
          newVal: v.new_value ?? "—",
        }))
      : listing.changedFields.map((f) => ({
          field: f,
          oldVal: listing[f as keyof Listing] as string ?? "—",
          newVal: "— (pending confirmation)",
        }));

  return (
    <div
      className={[
        "bg-surface rounded-lg shadow-sm p-5 flex flex-col gap-4",
        resolved ? "opacity-60" : "",
      ].join(" ")}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="font-semibold text-text">{listing.name}</p>
          <p className="text-xs text-text-muted mt-0.5">{listing.category} · {listing.address}</p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {isAccepted && (
            <span className="text-xs font-medium bg-success/10 text-success px-2 py-1 rounded">
              ✓ Accepted
            </span>
          )}
          {isRejected && (
            <span className="text-xs font-medium bg-red-100 text-red-600 px-2 py-1 rounded">
              ✗ Rejected
            </span>
          )}
          {!resolved && (
            <span className="text-xs font-medium bg-warning/20 text-warning px-2 py-1 rounded">
              ✉ Email sent — awaiting reply
            </span>
          )}
          {listing.confidence != null && (
            <ConfidenceBar value={listing.confidence} />
          )}
        </div>
      </div>

      {/* Changed fields */}
      {fieldRows.length > 0 && (
        <div className="bg-surface-dark rounded-lg px-4 py-3 flex flex-col gap-2">
          {fieldRows.map((row) => (
            <div key={row.field} className="flex items-center gap-3 text-sm">
              <span className="text-xs uppercase text-text-inverse-muted w-28 shrink-0">
                {row.field}
              </span>
              <span className="line-through text-text-inverse-muted">{row.oldVal}</span>
              <span className="text-text-inverse-muted">→</span>
              <span className="text-success font-medium">{row.newVal}</span>
            </div>
          ))}
        </div>
      )}

      {/* Actions */}
      {!resolved && onAccept && onReject && (
        <div className="flex gap-3">
          <button
            type="button"
            disabled={busy}
            onClick={onAccept}
            className="flex-1 bg-primary text-text-inverse rounded-lg py-2.5 text-sm font-semibold hover:bg-primary-hover disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {busy ? "Processing…" : "Accept change"}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={onReject}
            className="flex-1 border border-border text-text rounded-lg py-2.5 text-sm font-semibold hover:bg-surface-muted disabled:opacity-60 disabled:cursor-not-allowed"
          >
            Reject change
          </button>
        </div>
      )}
    </div>
  );
}
