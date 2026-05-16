interface KpiCardProps {
  label: string;
  value: number | string;
  delta?: string;
  deltaTone?: "success" | "warning" | "neutral";
}

export function KpiCard({ label, value, delta, deltaTone }: KpiCardProps) {
  const deltaClass =
    deltaTone === "success"
      ? "text-success"
      : deltaTone === "warning"
        ? "text-warning"
        : "text-text-muted";

  return (
    <div className="bg-surface rounded-lg shadow-sm p-6">
      <p className="text-4xl font-bold text-text">{value}</p>
      <p className="text-xs font-medium uppercase tracking-wide text-text-muted mt-2">
        {label}
      </p>
      {delta !== undefined && (
        <p className={`text-sm mt-1 ${deltaClass}`}>{delta}</p>
      )}
    </div>
  );
}
