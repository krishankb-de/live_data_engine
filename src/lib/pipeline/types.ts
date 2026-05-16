import type { MonitoredField, FieldUpdateStatus } from "../../models/types";

export type DetectSignal = "sitemap" | "etag" | "last_modified" | "offline" | "no_change";

export interface DetectResult {
  changed: boolean;
  signal: DetectSignal;
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

export interface ExtractResult {
  updates: FieldUpdate[];
}

export interface Confidence {
  value: number;
  status: FieldUpdateStatus;
}

export interface SourceFixture {
  listingId: string;
  sitemap_lastmod_changed: boolean;
  etag_old: string;
  etag_new: string;
  last_modified_old: string;
  last_modified_new: string;
  source_html: string;
}
