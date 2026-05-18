import { useCallback, useEffect, useState } from "react";
import { KpiCard } from "../../components/KpiCard";
import { api, type CostRow, type CostTotals } from "../../lib/api";

const EMPTY_TOTALS: CostTotals = {
  llm_calls: 0,
  llm_cost_eur: 0,
  http_requests: 0,
  listings_processed: 0,
};

function formatEur(value: number): string {
  return value.toLocaleString("de-DE", { style: "currency", currency: "EUR", maximumFractionDigits: 4 });
}

function fmtDay(day: string): string {
  if (!day) return "—";
  try {
    return new Date(day).toLocaleDateString("de-DE");
  } catch {
    return day;
  }
}

export function CostsPage() {
  const [items, setItems] = useState<CostRow[]>([]);
  const [totals, setTotals] = useState<CostTotals>(EMPTY_TOTALS);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await api.getCosts();
      setItems(res.items);
      setTotals(res.totals);
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
        <h1 className="text-2xl font-semibold text-text">Costs</h1>
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
        <p className="mt-4 text-sm text-red-600">Failed to load costs: {error}</p>
      )}

      <div className="grid grid-cols-4 gap-6 mt-6">
        <KpiCard label="LLM Calls" value={totals.llm_calls} />
        <KpiCard label="LLM Cost" value={formatEur(totals.llm_cost_eur)} />
        <KpiCard label="HTTP Requests" value={totals.http_requests} />
        <KpiCard label="Listings Processed" value={totals.listings_processed} />
      </div>

      <h2 className="text-base font-semibold text-text mt-8">Daily breakdown</h2>

      <div className="mt-4 bg-surface rounded-lg shadow-sm overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-surface-muted text-text-muted text-xs uppercase">
            <tr>
              <th className="text-left px-4 py-3 font-medium">Day</th>
              <th className="text-right px-4 py-3 font-medium">LLM calls</th>
              <th className="text-right px-4 py-3 font-medium">Tokens in</th>
              <th className="text-right px-4 py-3 font-medium">Tokens out</th>
              <th className="text-right px-4 py-3 font-medium">Cost</th>
              <th className="text-right px-4 py-3 font-medium">HTTP</th>
              <th className="text-right px-4 py-3 font-medium">Listings</th>
            </tr>
          </thead>
          <tbody>
            {items.length === 0 && !loading && (
              <tr>
                <td colSpan={7} className="px-4 py-6 text-center text-text-muted">No cost data yet.</td>
              </tr>
            )}
            {items.map((row) => (
              <tr key={row.day} className="border-t border-border">
                <td className="px-4 py-3 text-text">{fmtDay(row.day)}</td>
                <td className="px-4 py-3 text-right text-text">{row.llm_calls}</td>
                <td className="px-4 py-3 text-right text-text-muted">{row.llm_tokens_in.toLocaleString("de-DE")}</td>
                <td className="px-4 py-3 text-right text-text-muted">{row.llm_tokens_out.toLocaleString("de-DE")}</td>
                <td className="px-4 py-3 text-right text-text">{formatEur(row.llm_cost_eur)}</td>
                <td className="px-4 py-3 text-right text-text-muted">{row.http_requests}</td>
                <td className="px-4 py-3 text-right text-text">{row.listings_processed}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="mt-2 text-sm text-text">
        Showing {items.length} day{items.length === 1 ? "" : "s"}
      </p>
    </>
  );
}
