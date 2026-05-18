import type { FieldUpdate } from "../models/types";
import type { VersionOut } from "../lib/api";
import { ConfidenceBar } from "./ConfidenceBar";

interface DiffPanelProps {
  updates: FieldUpdate[];
  versions?: VersionOut[];
  onSendApproval?: () => void;
  sendingApproval?: boolean;
}

function latestPerField(versions: VersionOut[]): VersionOut[] {
  const byField = new Map<string, VersionOut>();
  for (const v of versions) {
    const prev = byField.get(v.field);
    const vTs = v.created_at ?? "";
    const prevTs = prev?.created_at ?? "";
    if (!prev || vTs.localeCompare(prevTs) > 0) byField.set(v.field, v);
  }
  return [...byField.values()];
}

export function DiffPanel({ updates, versions, onSendApproval, sendingApproval }: DiffPanelProps) {
  const versionRows = versions && versions.length > 0 ? latestPerField(versions) : [];

  return (
    <div className="bg-surface-dark rounded-lg p-6 text-text-inverse">
      <p className="text-lg font-semibold text-text-inverse mb-4">Changed Fields</p>

      {versionRows.length > 0 ? (
        <div className="flex flex-col">
          {versionRows.map((v, index) => {
            const hasOld = v.old_value != null && v.old_value !== "";
            return (
              <div
                key={v.id}
                className={index < versionRows.length - 1 ? "pb-4 mb-4 border-b border-white/10" : ""}
              >
                <p className="text-sm uppercase text-text-inverse-muted mb-1">{v.field}</p>
                <div className="flex items-center gap-2 text-base">
                  {hasOld ? (
                    <span className="line-through text-text-inverse-muted">{v.old_value}</span>
                  ) : (
                    <span className="italic text-text-inverse-muted opacity-70">(initial capture)</span>
                  )}
                  <span className="text-text-inverse-muted">→</span>
                  <span className="text-success font-medium">{v.new_value ?? "—"}</span>
                </div>
              </div>
            );
          })}
        </div>
      ) : updates.length === 0 ? (
        <p className="text-text-inverse-muted text-base">No changes detected</p>
      ) : (
        <div className="flex flex-col">
          {updates.map((update, index) => (
            <div
              key={`${update.listingId}-${update.field}`}
              className={`flex items-center justify-between gap-4 ${
                index < updates.length - 1 ? "pb-4 mb-4 border-b border-white/10" : ""
              }`}
            >
              <div className="min-w-0 flex-1">
                <p className="text-sm uppercase text-text-inverse-muted mb-1">{update.field}</p>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="line-through text-text-inverse-muted text-base">{update.oldValue}</span>
                  <span className="text-text-inverse-muted text-base">→</span>
                  <span className="text-success text-base font-medium">{update.newValue}</span>
                  {update.via === "llm-stub" && (
                    <span className="ml-2 text-sm bg-warning/20 text-warning px-1.5 py-0.5 rounded">
                      LLM escalated
                    </span>
                  )}
                </div>
              </div>
              <div className="shrink-0">
                <ConfidenceBar value={update.confidence} />
              </div>
            </div>
          ))}
        </div>
      )}

      {onSendApproval && (
        <button
          type="button"
          onClick={onSendApproval}
          disabled={sendingApproval}
          className="mt-5 w-full bg-warning text-text rounded-lg py-3 text-sm font-semibold hover:opacity-90 disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {sendingApproval ? "Sending…" : "Send email for approval"}
        </button>
      )}
    </div>
  );
}
