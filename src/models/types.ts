export type FieldUpdateStatus = "auto_applied" | "needs_review" | "rejected" | "source_offline";

export type MonitoredField = "address" | "phone" | "email" | "website" | "opening_hours";

export interface Listing {
  id: string;
  name: string;
  category: string;
  tier: "A" | "B";
  address: string;
  phone: string;
  email: string;
  website: string;
  opening_hours: string;
  lastRunAt: string | null;
  status: FieldUpdateStatus | null;
  confidence: number | null;
  changedFields: MonitoredField[];
  emailSent: boolean;
  live?: boolean;
}

export interface FieldUpdate {
  listingId: string;
  field: MonitoredField;
  oldValue: string;
  newValue: string;
  confidence: number;
  status: FieldUpdateStatus;
  via?: "llm-stub";
}

export interface PipelineRun {
  id: string;
  startedAt: string;
  listings: Listing[];
  fieldUpdates: FieldUpdate[];
  trigger: "manual" | "auto-poll";
}

export interface KpiData {
  entriesChecked: number;
  autoUpdates: number;
  needsReview: number;
  updatesToday: number;
}
