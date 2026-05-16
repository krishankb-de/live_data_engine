import type { Listing } from "../models/types";
import { StatusPill } from "./StatusPill";
import { ConfidenceBar } from "./ConfidenceBar";

interface ListingsTableProps {
  listings: Listing[];
  onRowClick: (listing: Listing) => void;
}

const COLUMNS = [
  "Business",
  "Tier",
  "Changed Fields",
  "Confidence",
  "Status",
  "Email",
  "Action",
] as const;

export function ListingsTable({ listings, onRowClick }: ListingsTableProps) {
  return (
    <table className="w-full text-sm">
      <thead>
        <tr className="border-b border-border">
          {COLUMNS.map((col) => (
            <th
              key={col}
              className="text-left px-4 py-3 text-xs font-bold uppercase tracking-wide text-text-muted"
            >
              {col}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {listings.length === 0 ? (
          <tr>
            <td colSpan={7} className="px-4 py-8 text-center text-text-muted">
              Run pipeline to load data
            </td>
          </tr>
        ) : (
          listings.map((listing) => (
            <tr
              key={listing.id}
              className={[
                "border-b border-border last:border-0 hover:bg-surface-muted cursor-pointer",
                listing.live ? "border-l-2 border-accent" : "",
              ]
                .join(" ")
                .trim()}
              onClick={() => onRowClick(listing)}
            >
              <td className="px-4 py-4">
                <p className="font-medium text-text">{listing.name}</p>
                <p className="text-xs text-text-muted mt-0.5">
                  {listing.category}
                </p>
              </td>
              <td className="px-4 py-4">
                <span className="text-xs font-medium bg-surface-muted px-2 py-0.5 rounded">
                  {listing.tier}
                </span>
              </td>
              <td className="px-4 py-4 text-text-muted">
                {listing.changedFields.length > 0
                  ? listing.changedFields.join(", ")
                  : "—"}
              </td>
              <td className="px-4 py-4">
                <ConfidenceBar value={listing.confidence} />
              </td>
              <td className="px-4 py-4">
                <StatusPill status={listing.status} />
              </td>
              <td className="px-4 py-4 text-text-muted text-xs">
                {listing.emailSent ? "✉ Sent" : "—"}
              </td>
              <td className="px-4 py-4">
                <button
                  className="text-sm text-primary font-medium hover:underline"
                  onClick={(e) => {
                    e.stopPropagation();
                    onRowClick(listing);
                  }}
                >
                  Details →
                </button>
              </td>
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
