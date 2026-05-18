import type { Listing } from "../models/types";
import type { FieldObs } from "../lib/api";

interface BusinessCardProps {
  listing: Listing;
  observations?: FieldObs[];
}

interface FieldRow {
  label: string;
  field: string | null;
  value: string;
}

export function BusinessCard({ listing, observations = [] }: BusinessCardProps) {
  const obsByField = new Map(observations.map((o) => [o.field, o]));

  const rows: FieldRow[] = [
    { label: "Address", field: "address", value: listing.address },
    { label: "Phone", field: "phone", value: listing.phone },
    { label: "Email", field: null, value: listing.email },
    { label: "Website", field: null, value: listing.website },
    { label: "Opening Hours", field: "opening_hours", value: listing.opening_hours },
    {
      label: "Check Interval",
      field: null,
      value: listing.checkIntervalDays != null ? `${listing.checkIntervalDays}d` : "",
    },
    {
      label: "Verifiable",
      field: null,
      value: listing.isVerifiable ? "Yes" : "No",
    },
  ];

  return (
    <div className="bg-surface rounded-lg p-6">
      <div className="bg-accent rounded-t-lg -mx-6 -mt-6 px-6 py-4 mb-4 flex items-start justify-between">
        <div>
          <p className="text-lg font-bold text-text">{listing.name}</p>
        </div>
        <span className="text-xs bg-primary text-text-inverse px-2 py-0.5 rounded font-medium">
          Tier {listing.tier}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3">
        {rows.map(({ label, field, value }) => {
          const obs = field ? obsByField.get(field) : undefined;
          const confidence = obs?.extraction_confidence;
          return (
            <div key={label}>
              <p className="text-xs uppercase text-text-muted font-medium">{label}</p>
              <p className="text-sm text-text">{value || "—"}</p>
              {obs && (obs.source_url || confidence != null) && (
                <p className="text-xs text-text-muted mt-0.5 flex items-center gap-1.5">
                  {obs.source_url && (
                    <a
                      href={obs.source_url}
                      target="_blank"
                      rel="noreferrer"
                      className="uppercase tracking-wide font-medium text-primary hover:underline"
                    >
                      source
                    </a>
                  )}
                  {confidence != null && (
                    <span aria-label={`extraction confidence ${(confidence * 100).toFixed(0)}%`}>
                      {obs.source_url ? "· " : ""}{(confidence * 100).toFixed(0)}%
                    </span>
                  )}
                </p>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
