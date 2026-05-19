import type { Listing } from "../models/types";

interface ListingsTableProps {
  listings: Listing[];
  onRowClick: (listing: Listing) => void;
}

const COLUMNS = [
  "Business",
  "Tier",
  "Website",
  "Interval",
  "Verifiable",
  "Action",
] as const;

function hostname(url: string): string {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return url;
  }
}

export function ListingsTable({ listings, onRowClick }: ListingsTableProps) {
  return (
    <div className="overflow-x-auto">
    <table className="w-full text-sm min-w-[560px]">
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
            <td colSpan={COLUMNS.length} className="px-4 py-8 text-center text-text-muted">
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
              </td>
              <td className="px-4 py-4">
                <span className="text-xs font-medium bg-surface-muted px-2 py-0.5 rounded">
                  {listing.tier}
                </span>
              </td>
              <td className="px-4 py-4 text-sm">
                {listing.website ? (
                  <a
                    href={listing.website}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-blue-600 underline"
                  >
                    {hostname(listing.website)}
                  </a>
                ) : (
                  <span className="text-text-muted">—</span>
                )}
              </td>
              <td className="px-4 py-4 text-sm text-text-muted">
                {listing.checkIntervalDays != null
                  ? listing.checkIntervalDays < 1
                    ? `${(listing.checkIntervalDays * 24).toFixed(1)}H`
                    : `${listing.checkIntervalDays}d`
                  : "—"}
              </td>
              <td className="px-4 py-4 text-sm">
                {listing.isVerifiable ? (
                  <span className="text-emerald-600" aria-label="verifiable">✓</span>
                ) : (
                  <span className="text-text-muted" aria-label="not verifiable">✗</span>
                )}
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
    </div>
  );
}
