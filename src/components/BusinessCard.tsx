import type { Listing } from "../models/types";

export function BusinessCard({ listing }: { listing: Listing }) {
  const fields: { label: string; value: string }[] = [
    { label: "Address", value: listing.address },
    { label: "Phone", value: listing.phone },
    { label: "Email", value: listing.email },
    { label: "Website", value: listing.website },
    { label: "Opening Hours", value: listing.opening_hours },
  ];

  return (
    <div className="bg-surface rounded-lg p-6">
      <div className="bg-accent rounded-t-lg -mx-6 -mt-6 px-6 py-4 mb-4 flex items-start justify-between">
        <div>
          <p className="text-lg font-bold text-text">{listing.name}</p>
          <p className="text-sm text-text-muted">{listing.category}</p>
        </div>
        <span className="text-xs bg-primary text-text-inverse px-2 py-0.5 rounded font-medium">
          Tier {listing.tier}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-3">
        {fields.map(({ label, value }) => (
          <div key={label}>
            <p className="text-xs uppercase text-text-muted font-medium">{label}</p>
            <p className="text-sm text-text">{value}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
