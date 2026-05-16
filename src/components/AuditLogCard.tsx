import type { Listing, FieldUpdate } from "../models/types";

export function AuditLogCard({ listing, updates }: { listing: Listing; updates: FieldUpdate[] }) {
  const rows: { key: string; value: string }[] = [
    {
      key: "Timestamp",
      value: listing.lastRunAt ? new Date(listing.lastRunAt).toLocaleString("de-DE") : "—",
    },
    { key: "Business", value: listing.name },
    { key: "Source", value: listing.website },
    { key: "Trigger", value: `Manual run · Tier ${listing.tier}` },
    { key: "Fields changed", value: updates.map((u) => u.field).join(", ") || "—" },
    { key: "Confidence", value: updates[0]?.confidence?.toFixed(2) ?? "—" },
    { key: "Email sent", value: listing.emailSent ? "Yes" : "No" },
  ];

  return (
    <div className="bg-surface-dark rounded-lg p-6">
      <p className="text-sm font-semibold text-text-inverse mb-4">Pipeline Audit Log</p>

      <div className="flex flex-col gap-2">
        {rows.map(({ key, value }) => (
          <div key={key} className="flex gap-2 text-sm">
            <span className="text-text-inverse-muted w-32 shrink-0">{key}</span>
            <span className="text-text-inverse">{value}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
