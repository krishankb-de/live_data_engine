import { useCallback, useEffect, useState } from "react";
import { api, type BatchStatus } from "../../lib/api";

const PAGE_SIZE = 20;

function statusTone(status: string): string {
  switch (status) {
    case "done":
      return "bg-emerald-100 text-emerald-700";
    case "running":
      return "bg-blue-100 text-blue-700";
    case "queued":
      return "bg-amber-100 text-amber-700";
    case "failed":
      return "bg-red-100 text-red-700";
    default:
      return "bg-surface-muted text-text-muted";
  }
}

function fmt(ts: string | null | undefined): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("de-DE");
  } catch {
    return ts;
  }
}

function duration(started?: string | null, finished?: string | null): string {
  if (!started) return "—";
  if (!finished) return "running…";
  const ms = new Date(finished).getTime() - new Date(started).getTime();
  if (!Number.isFinite(ms) || ms < 0) return "—";
  if (ms < 1000) return `${ms}ms`;
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const r = s % 60;
  return `${m}m ${r}s`;
}

function formatEur(value: number): string {
  return value.toLocaleString("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 4 });
}

export function BatchesPage() {
  const [items, setItems] = useState<BatchStatus[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.listBatches({ limit: PAGE_SIZE, offset: 0 });
      setItems(res.items);
      setTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <>
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-semibold text-text">Pipeline Status</h1>
        <button
          type="button"
          disabled={loading}
          onClick={() => { void refresh(); }}
          className="bg-primary text-text-inverse rounded-lg px-4 py-2 text-sm font-medium hover:bg-primary-hover disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>

      {error && (
        <p className="mt-4 text-sm text-red-600">Failed to load batches: {error}</p>
      )}

      <div className="mt-6 bg-surface rounded-lg shadow-sm overflow-hidden">
          <table className="w-full text-sm">
            <thead className="bg-surface-muted text-text-muted text-xs uppercase">
              <tr>
                <th className="text-left px-4 py-3 font-medium">Batch</th>
                <th className="text-left px-4 py-3 font-medium">Status</th>
                <th className="text-left px-4 py-3 font-medium">Started</th>
                <th className="text-left px-4 py-3 font-medium">Duration</th>
                <th className="text-right px-4 py-3 font-medium">Listings</th>
                <th className="text-right px-4 py-3 font-medium">Proposed</th>
                <th className="text-right px-4 py-3 font-medium">Auto</th>
                <th className="text-right px-4 py-3 font-medium">Review</th>
                <th className="text-right px-4 py-3 font-medium">LLM calls</th>
                <th className="text-right px-4 py-3 font-medium">Cost</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 && !loading && (
                <tr>
                  <td colSpan={10} className="px-4 py-6 text-center text-text-muted">No batches yet.</td>
                </tr>
              )}
              {items.map((b) => (
                <tr key={b.id} className="border-t border-border">
                  <td className="px-4 py-3 font-mono text-text">#{b.id}</td>
                  <td className="px-4 py-3">
                    <span className={`px-2 py-0.5 rounded text-xs font-medium ${statusTone(b.status)}`}>
                      {b.status}
                    </span>
                    {b.anomaly_flagged && (
                      <span className="ml-2 text-xs text-red-600" title={b.anomaly_reason ?? "anomaly"}>⚠</span>
                    )}
                  </td>
                  <td className="px-4 py-3 text-text-muted">{fmt(b.started_at)}</td>
                  <td className="px-4 py-3 text-text-muted">{duration(b.started_at, b.finished_at)}</td>
                  <td className="px-4 py-3 text-right text-text">{b.listings_processed}</td>
                  <td className="px-4 py-3 text-right text-text">{b.changes_proposed}</td>
                  <td className="px-4 py-3 text-right text-emerald-700">{b.changes_auto_applied}</td>
                  <td className="px-4 py-3 text-right text-amber-700">{b.changes_review_queue}</td>
                  <td className="px-4 py-3 text-right text-text-muted">{b.llm_calls}</td>
                  <td className="px-4 py-3 text-right text-text">{formatEur(b.cost_eur)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

      <p className="mt-2 text-sm text-text">
        Showing {items.length} of {total}
      </p>
    </>
  );
}
