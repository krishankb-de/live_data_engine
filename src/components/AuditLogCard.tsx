import type { Listing, FieldUpdate } from "../models/types";
import type { VersionOut } from "../lib/api";

interface AuditLogCardProps {
  listing: Listing;
  updates: FieldUpdate[];
  versions?: VersionOut[];
}

const STRICTNESS: Record<string, number> = {
  needs_review: 5,
  rejected: 4,
  discarded: 3,
  auto_applied: 2,
  applied: 1,
  pending: 0,
};

function decisionTone(decision: string | null | undefined): string {
  switch (decision) {
    case "auto_applied":
    case "applied":
      return "bg-emerald-500/20 text-emerald-300";
    case "needs_review":
      return "bg-amber-500/20 text-amber-300";
    case "rejected":
    case "discarded":
      return "bg-red-500/20 text-red-300";
    default:
      return "bg-white/10 text-text-inverse-muted";
  }
}

function strictest(decisions: (string | null | undefined)[]): string {
  let bestKey = "pending";
  let bestRank = -1;
  for (const d of decisions) {
    const key = d ?? "pending";
    const rank = STRICTNESS[key] ?? 0;
    if (rank > bestRank) {
      bestRank = rank;
      bestKey = key;
    }
  }
  return bestKey;
}

function fmt(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("de-DE");
  } catch {
    return ts;
  }
}

interface BatchBucket {
  key: string;
  batchId: number | null;
  versions: VersionOut[];
  earliest: string;
}

function groupByBatch(versions: VersionOut[]): BatchBucket[] {
  const buckets = new Map<string, BatchBucket>();
  for (const v of versions) {
    const key = v.batch_id != null ? String(v.batch_id) : "orphan";
    let bucket = buckets.get(key);
    if (!bucket) {
      bucket = {
        key,
        batchId: v.batch_id ?? null,
        versions: [],
        earliest: v.created_at ?? "",
      };
      buckets.set(key, bucket);
    }
    bucket.versions.push(v);
    const ts = v.created_at ?? "";
    if (!bucket.earliest || (ts && ts.localeCompare(bucket.earliest) < 0)) {
      bucket.earliest = ts;
    }
  }
  // Newest batch first (sort by earliest desc)
  return [...buckets.values()].sort((a, b) => b.earliest.localeCompare(a.earliest));
}

function formatConfidence(versions: VersionOut[]): string | null {
  const vals = versions
    .map((v) => v.intent_confidence)
    .filter((c): c is number => c != null);
  if (vals.length === 0) return null;
  const min = Math.min(...vals);
  const max = Math.max(...vals);
  if (min === max) return `${(min * 100).toFixed(0)}%`;
  return `${(min * 100).toFixed(0)}–${(max * 100).toFixed(0)}%`;
}

export function AuditLogCard({ listing, updates, versions = [] }: AuditLogCardProps) {
  if (versions.length === 0) {
    const rows: { key: string; value: string }[] = [
      { key: "Timestamp", value: fmt(listing.lastRunAt) },
      { key: "Business", value: listing.name },
      { key: "Source", value: listing.website },
      { key: "Trigger", value: `Manual run · Tier ${listing.tier}` },
      { key: "Fields changed", value: updates.map((u) => u.field).join(", ") || "—" },
      { key: "Confidence", value: updates[0]?.confidence?.toFixed(2) ?? "—" },
      { key: "Email sent", value: listing.emailSent ? "Yes" : "No" },
    ];

    return (
      <div className="bg-surface-dark rounded-lg p-6">
        <p className="text-lg font-semibold text-text-inverse mb-4">Pipeline Audit Log</p>
        <div className="flex flex-col gap-2">
          {rows.map(({ key, value }) => (
            <div key={key} className="flex gap-2 text-base">
              <span className="text-text-inverse-muted w-32 shrink-0">{key}</span>
              <span className="text-text-inverse">{value}</span>
            </div>
          ))}
        </div>
      </div>
    );
  }

  const buckets = groupByBatch(versions);

  return (
    <div className="bg-surface-dark rounded-lg p-6">
      <div className="flex items-center justify-between mb-4 gap-3">
        <p className="text-lg font-semibold text-text-inverse">Pipeline Audit Log</p>
        <span className="text-sm text-text-inverse-muted">
          {versions.length} version{versions.length === 1 ? "" : "s"}
        </span>
      </div>

      <ul className="flex flex-col gap-4">
        {buckets.map((bucket) => {
          const decision = strictest(bucket.versions.map((v) => v.decision));
          const confidence = formatConfidence(bucket.versions);
          const fields = [...new Set(bucket.versions.map((v) => v.field))].join(", ");
          const sample = bucket.versions[0];
          const who = sample.applied_by ?? sample.reviewed_by ?? "system";
          const when = fmt(bucket.earliest);
          return (
            <li key={bucket.key} className="border-l-2 border-white/10 pl-3 flex flex-col gap-1">
              <div className="flex items-center gap-2 text-sm flex-wrap">
                <span className="font-mono text-text-inverse">
                  {bucket.batchId != null ? `Batch #${bucket.batchId}` : "Unassigned"}
                </span>
                <span className={`px-1.5 py-0.5 rounded text-xs uppercase tracking-wide ${decisionTone(decision)}`}>
                  {decision}
                </span>
                {confidence && (
                  <span className="text-text-inverse-muted">{confidence}</span>
                )}
              </div>
              <div className="text-sm text-text-inverse-muted">
                fields: <span className="font-mono">{fields}</span>
              </div>
              <div className="text-sm text-text-inverse-muted">
                {who} · {when}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
