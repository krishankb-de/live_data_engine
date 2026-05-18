import { useBrainMetrics } from "../hooks/useBrainMetrics";
import type { BrainCandidateOut } from "../lib/api";

const STATUS_COLORS: Record<string, string> = {
  trial:    "bg-blue-100 text-blue-700",
  active:   "bg-green-100 text-green-700",
  stale:    "bg-yellow-100 text-yellow-700",
  disabled: "bg-gray-100 text-gray-500",
};

function StatusPill({ s, count }: { s: string; count: number }) {
  const cls = STATUS_COLORS[s] ?? "bg-surface-muted text-text-muted";
  return (
    <span className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium ${cls}`}>
      {s} <span className="font-bold">{count}</span>
    </span>
  );
}

function DecisionRow({ c }: { c: BrainCandidateOut }) {
  const isPromoted = c.status === "promoted";
  return (
    <li className="flex items-center gap-2 text-xs text-text truncate py-0.5">
      <span className={`shrink-0 font-semibold ${isPromoted ? "text-success" : "text-warning"}`}>
        {isPromoted ? "✓" : "✗"}
      </span>
      <span className="font-mono truncate">{c.candidate_pattern}</span>
      <span className="shrink-0 text-text-muted">{c.field}</span>
    </li>
  );
}

export function BrainStatusPanel() {
  const { metrics, error } = useBrainMetrics();

  if (error) {
    return (
      <div className="bg-surface rounded-lg shadow-sm p-4 col-span-full">
        <p className="text-xs text-warning">Brain metrics unavailable: {error}</p>
      </div>
    );
  }

  if (!metrics) {
    return (
      <div className="bg-surface rounded-lg shadow-sm p-4 col-span-full animate-pulse">
        <div className="h-4 w-32 bg-surface-muted rounded" />
      </div>
    );
  }

  const totalPatterns = Object.values(metrics.pattern_counts).reduce((a, b) => a + b, 0);

  return (
    <div className="bg-surface rounded-lg shadow-sm p-5 col-span-full">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-text">Self-Learning Brain</h3>
        <span className="text-xs text-text-muted">{totalPatterns} pattern{totalPatterns !== 1 ? "s" : ""}</span>
      </div>

      <div className="flex flex-wrap gap-2 mb-4">
        {Object.entries(metrics.pattern_counts).map(([s, n]) => (
          <StatusPill key={s} s={s} count={n} />
        ))}
        {totalPatterns === 0 && (
          <span className="text-xs text-text-muted">No patterns yet</span>
        )}
      </div>

      <div className="flex gap-6 text-xs mb-4">
        <div>
          <p className="text-text-muted uppercase tracking-wide">Cost today</p>
          <p className="text-text font-semibold">€{metrics.cost_today_eur.toFixed(4)}</p>
        </div>
        <div>
          <p className="text-text-muted uppercase tracking-wide">Accept rate</p>
          <p className="text-text font-semibold">
            {metrics.accept_rate !== null ? `${(metrics.accept_rate * 100).toFixed(0)}%` : "—"}
          </p>
        </div>
      </div>

      {metrics.recent_decisions.length > 0 && (
        <div>
          <p className="text-xs text-text-muted uppercase tracking-wide mb-1">Recent decisions</p>
          <ul className="space-y-0.5">
            {metrics.recent_decisions.slice(0, 10).map((c) => (
              <DecisionRow key={c.id} c={c} />
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
