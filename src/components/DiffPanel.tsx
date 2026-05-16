import type { FieldUpdate } from "../models/types";
import { ConfidenceBar } from "./ConfidenceBar";

export function DiffPanel({ updates }: { updates: FieldUpdate[] }) {
  return (
    <div className="bg-surface-dark rounded-lg p-6 text-text-inverse">
      <p className="text-sm font-semibold text-text-inverse mb-4">Changed Fields</p>

      {updates.length === 0 ? (
        <p className="text-text-inverse-muted text-sm">No changes detected</p>
      ) : (
        <div className="flex flex-col">
          {updates.map((update, index) => (
            <div
              key={`${update.listingId}-${update.field}`}
              className={index < updates.length - 1 ? "pb-4 mb-4 border-b border-white/10" : ""}
            >
              <p className="text-xs uppercase text-text-inverse-muted mb-1">{update.field}</p>
              <div className="flex items-center gap-2 mb-2">
                <span className="line-through text-text-inverse-muted text-sm">{update.oldValue}</span>
                <span className="text-text-inverse-muted text-sm">→</span>
                <span className="text-success text-sm font-medium">{update.newValue}</span>
                {update.via === "llm-stub" && (
                  <span className="ml-2 text-xs bg-warning/20 text-warning px-1.5 py-0.5 rounded">
                    LLM escalated
                  </span>
                )}
              </div>
              <ConfidenceBar value={update.confidence} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
