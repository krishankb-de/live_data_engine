import type { Listing, FieldUpdateStatus } from "../../models/types";
import type { FieldUpdate } from "./types";

// Higher number = more urgent; the listing inherits the worst status across all its updates
const STATUS_PRIORITY: Record<FieldUpdateStatus, number> = {
  source_offline: 3,
  needs_review: 2,
  auto_applied: 1,
  rejected: 0,
};

export function applyUpdates(listing: Listing, updates: FieldUpdate[]): Listing {
  const copy = { ...listing } as Listing & Record<string, string>;

  // Only write field values for high-confidence updates; needs_review holds the new value
  // in the FieldUpdate object until a human accepts it
  for (const update of updates) {
    if (update.status === "auto_applied") {
      copy[update.field] = update.newValue;
    }
  }

  copy.lastRunAt = new Date().toISOString();

  // Average confidence across all updated fields shown in the listing row
  if (updates.length === 0) {
    copy.confidence = null;
  } else {
    const sum = updates.reduce((acc, u) => acc + u.confidence, 0);
    copy.confidence = sum / updates.length;
  }

  copy.changedFields = updates.map((u) => u.field);

  // Any needs_review update triggers an email notification flag
  copy.emailSent = updates.some((u) => u.status === "needs_review");

  // The listing's overall status reflects the most urgent individual field status
  const highestStatus = updates.reduce<FieldUpdateStatus | null>((best, u) => {
    if (best === null) return u.status;
    return STATUS_PRIORITY[u.status] > STATUS_PRIORITY[best] ? u.status : best;
  }, null);

  if (highestStatus !== null) {
    copy.status = highestStatus;
  }

  return copy as Listing;
}
