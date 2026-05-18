import { useEffect, useState } from "react";
import { useParams, Link } from "react-router";
import { BusinessCard } from "@/components/BusinessCard";
import { DiffPanel } from "@/components/DiffPanel";
import { AuditLogCard } from "@/components/AuditLogCard";
import { api, type FieldObs, type ListingOut, type VersionOut } from "@/lib/api";
import type { Listing } from "@/models/types";

function toListing(row: ListingOut): Listing {
  return {
    id: String(row.id),
    name: row.name,
    category: row.category ?? "",
    tier: "B",
    address: row.address ?? "",
    phone: row.phone ?? "",
    email: "",
    website: row.website_url ?? "",
    opening_hours: row.opening_hours ?? "",
    lastRunAt: row.last_checked ?? null,
    status: null,
    confidence: null,
    changedFields: [],
    emailSent: false,
    checkIntervalDays: row.check_interval_days ?? null,
    isVerifiable: row.is_verifiable ?? true,
  };
}

export function ListingDetailPage() {
  const { id } = useParams<{ id: string }>();
  const numId = Number(id);
  const [listing, setListing] = useState<Listing | null>(null);
  const [observations, setObservations] = useState<FieldObs[]>([]);
  const [versions, setVersions] = useState<VersionOut[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!Number.isFinite(numId)) {
      setError("Invalid listing id");
      return;
    }
    let cancelled = false;
    void Promise.all([api.getListing(numId), api.getListingVersions(numId)])
      .then(([detail, vers]) => {
        if (cancelled) return;
        setListing(toListing(detail.listing));
        setObservations(detail.latest_observations);
        setVersions(vers);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Failed to load listing");
      });
    return () => {
      cancelled = true;
    };
  }, [numId]);

  return (
    <div className="min-h-screen bg-surface-muted py-10 px-4">
      <div className="max-w-4xl mx-auto">
        <div className="mb-6 flex items-center justify-between">
          <span className="text-xs uppercase tracking-wider text-text-muted">
            Review &amp; update listing
          </span>
          <Link to="/dashboard" className="text-sm text-primary hover:underline">
            Dashboard &rarr;
          </Link>
        </div>

        {error && (
          <div className="bg-surface rounded-lg p-6 text-sm text-red-600">
            {error}
          </div>
        )}

        {!error && !listing && (
          <div className="bg-surface rounded-lg p-6 text-sm text-text-muted">
            Loading listing…
          </div>
        )}

        {listing && (
          <div className="bg-surface rounded-xl shadow-lg p-6 flex flex-col gap-4">
            <BusinessCard listing={listing} observations={observations} />
            <DiffPanel updates={[]} versions={versions} />
            <AuditLogCard listing={listing} updates={[]} versions={versions} />
          </div>
        )}
      </div>
    </div>
  );
}
