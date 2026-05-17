import type { FieldUpdateStatus } from "../models/types";

export function StatusPill({ status }: { status: FieldUpdateStatus | null }) {
  if (status === null) {
    return <span className="text-text-muted text-xs">—</span>;
  }

  if (status === "auto_applied") {
    return (
      <span className="bg-emerald-100 text-emerald-700 rounded-md px-2 py-0.5 text-xs font-medium">
        AUTO-UPDATE
      </span>
    );
  }

  if (status === "needs_review") {
    return (
      <span className="bg-amber-100 text-amber-700 rounded-md px-2 py-0.5 text-xs font-medium">
        NEEDS REVIEW
      </span>
    );
  }

  return (
    <span className="bg-red-100 text-red-700 rounded-md px-2 py-0.5 text-xs font-medium">
      WEBSITE OFFLINE
    </span>
  );
}
