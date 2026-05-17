/** Typed fetch wrappers for the FastAPI backend. */

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "http://localhost:8000";
const API_KEY = (import.meta.env.VITE_API_KEY as string | undefined) ?? "";

// ---------------------------------------------------------------------------
// Backend schema types (mirror api/schemas.py)
// ---------------------------------------------------------------------------

export interface ListingOut {
  id: number;
  gs_listing_id: string;
  name: string;
  category?: string | null;
  address?: string | null;
  phone?: string | null;
  opening_hours?: string | null;
  website_url?: string | null;
  hash_value?: string | null;
  last_checked?: string | null;
  next_check?: string | null;
  check_interval_days?: number | null;
  consecutive_unchanged?: number | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface VersionOut {
  id: number;
  listing_id: number;
  batch_id?: number | null;
  field: string;
  old_value?: string | null;
  new_value?: string | null;
  intent_confidence?: number | null;
  decision?: string | null;
  signals?: unknown;
  reasoning?: string | null;
  applied_at?: string | null;
  applied_by?: string | null;
  reviewed_at?: string | null;
  reviewed_by?: string | null;
  created_at?: string | null;
}

export interface BatchStatus {
  id: number;
  status: string;
  started_at?: string | null;
  finished_at?: string | null;
  listings_processed: number;
  changes_proposed: number;
  changes_auto_applied: number;
  changes_review_queue: number;
  changes_discarded: number;
  llm_calls: number;
  cost_eur: number;
  anomaly_flagged: boolean;
  anomaly_reason?: string | null;
}

// ---------------------------------------------------------------------------
// Core fetch
// ---------------------------------------------------------------------------

async function apiFetch<T>(path: string, opts?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    ...opts,
    headers: {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json",
      ...(opts?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`API ${res.status}: ${detail || res.statusText}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// Endpoints
// ---------------------------------------------------------------------------

export const api = {
  postBatch: (phases: number[], testMode = false) =>
    apiFetch<{ batch_id: number; status: string }>("/api/batches", {
      method: "POST",
      body: JSON.stringify({ phases, test_mode: testMode }),
    }),

  getBatch: (id: number) => apiFetch<BatchStatus>(`/api/batches/${id}`),

  getListings: (params?: { q?: string; city?: string; limit?: number; offset?: number }) => {
    const p = new URLSearchParams();
    if (params?.q) p.set("q", params.q);
    if (params?.city) p.set("city", params.city);
    if (params?.limit !== undefined) p.set("limit", String(params.limit));
    if (params?.offset !== undefined) p.set("offset", String(params.offset));
    const qs = p.toString();
    return apiFetch<{ items: ListingOut[]; total: number }>(`/api/listings${qs ? `?${qs}` : ""}`);
  },

  getPendingReviews: (params?: { limit?: number; offset?: number }) => {
    const p = new URLSearchParams();
    if (params?.limit !== undefined) p.set("limit", String(params.limit));
    if (params?.offset !== undefined) p.set("offset", String(params.offset));
    const qs = p.toString();
    return apiFetch<{ items: VersionOut[]; total: number }>(`/api/reviews/pending${qs ? `?${qs}` : ""}`);
  },

  acceptVersion: (id: number) =>
    apiFetch<VersionOut>(`/api/versions/${id}/accept`, { method: "POST" }),

  rejectVersion: (id: number, reason?: string) =>
    apiFetch<VersionOut>(`/api/versions/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason: reason ?? null }),
    }),
};

// ---------------------------------------------------------------------------
// Batch polling helper
// ---------------------------------------------------------------------------

export async function pollBatch(
  batchId: number,
  onProgress?: (s: BatchStatus) => void,
  intervalMs = 2000,
  maxWaitMs = 120_000,
): Promise<BatchStatus> {
  const deadline = Date.now() + maxWaitMs;
  while (Date.now() < deadline) {
    const status = await api.getBatch(batchId);
    onProgress?.(status);
    if (status.status === "done" || status.status === "failed") return status;
    await new Promise<void>((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`batch ${batchId} timed out after ${maxWaitMs / 1000}s`);
}
